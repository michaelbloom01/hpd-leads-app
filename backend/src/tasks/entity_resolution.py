"""Graph-based entity resolution for lead aggregation.

Problem: A single PM company may register under multiple names,
officer names, or slightly different corporate names across HPD registrations.
This creates duplicate leads that should be merged.

Approach:
1. Build a graph where nodes are registration contacts/corporate names.
2. Add edges when two contacts share the same phone, address, or officer name.
3. Use connected components to identify clusters = single real-world entity.
4. Merge clusters into canonical leads with portfolio aggregation.
"""
import logging
from collections import defaultdict
from typing import Optional

import networkx as nx
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

try:
    from src.worker import app as celery_app
except ImportError:
    class _FakeCelery:
        @staticmethod
        def task(*args, **kwargs):
            return lambda fn: fn
    celery_app = _FakeCelery()

logger = logging.getLogger(__name__)


def _get_pg_session() -> Session:
    from src.db.session import get_sync_url
    engine = create_engine(get_sync_url())
    return Session(engine)


def _normalize(s: Optional[str]) -> str:
    if not s:
        return ""
    return " ".join(s.upper().strip().split())


def _build_entity_graph(session: Session) -> nx.Graph:
    """Build entity resolution graph from building contacts."""
    G = nx.Graph()

    contacts = session.execute(text("""
        SELECT bc.bbl, bc.registration_id, bc.contact_type, bc.description,
               bc.corporation_name, bc.first_name, bc.last_name,
               bc.business_address, bc.business_city, bc.business_state, bc.business_zip
        FROM building_contacts bc
        WHERE bc.contact_type IN ('CorporateOwner', 'IndividualOwner', 'Agent', 'HeadOfficer', 'Owner')
    """)).fetchall()

    logger.info(f"Building entity graph from {len(contacts)} contacts")

    name_to_nodes = defaultdict(set)
    address_to_nodes = defaultdict(set)

    for c in contacts:
        bbl, reg_id, ctype, desc, corp, first, last, addr, city, state, zip_code = c

        corp_name = _normalize(corp)
        person_name = _normalize(f"{first or ''} {last or ''}".strip())
        full_addr = _normalize(f"{addr or ''} {city or ''} {state or ''}")

        if corp_name:
            node_id = f"corp:{corp_name}"
            G.add_node(node_id, type="corporation", name=corp_name, bbls=set())
            G.nodes[node_id]["bbls"].add(bbl)
            name_to_nodes[corp_name].add(node_id)

            if full_addr and len(full_addr) > 5:
                address_to_nodes[full_addr].add(node_id)

        if person_name and len(person_name) > 3:
            node_id = f"person:{person_name}"
            if node_id not in G:
                G.add_node(node_id, type="person", name=person_name, bbls=set())
            G.nodes[node_id]["bbls"].add(bbl)

            if corp_name:
                G.add_edge(f"corp:{corp_name}", node_id, relation="officer_of")

            if full_addr and len(full_addr) > 5:
                address_to_nodes[full_addr].add(node_id)

    for addr, nodes in address_to_nodes.items():
        node_list = list(nodes)
        for i in range(len(node_list)):
            for j in range(i + 1, len(node_list)):
                G.add_edge(node_list[i], node_list[j], relation="shared_address")

    logger.info(f"Entity graph: {G.number_of_nodes()} nodes, {G.number_of_edges()} edges")
    return G


def _resolve_clusters(G: nx.Graph) -> list[dict]:
    """Extract connected components and create canonical lead representations."""
    components = list(nx.connected_components(G))
    logger.info(f"Found {len(components)} entity clusters")

    leads = []
    for component in components:
        bbls = set()
        names = []
        corp_names = []

        for node in component:
            data = G.nodes[node]
            bbls.update(data.get("bbls", set()))
            if data.get("type") == "corporation":
                corp_names.append(data["name"])
            elif data.get("type") == "person":
                names.append(data["name"])

        canonical_name = corp_names[0] if corp_names else names[0] if names else "Unknown"
        all_names = list(set(corp_names + names))

        leads.append({
            "canonical_name": canonical_name,
            "all_names": all_names,
            "bbls": list(bbls),
            "portfolio_size": len(bbls),
            "node_count": len(component),
            "graph_json": {
                "nodes": [{"id": n, **{k: v for k, v in G.nodes[n].items() if k != "bbls"}} for n in component],
                "edges": [{"source": u, "target": v, **d} for u, v, d in G.edges(component, data=True)],
            },
        })

    leads.sort(key=lambda x: x["portfolio_size"], reverse=True)
    return leads


@celery_app.task(bind=True, name="src.tasks.entity_resolution.resolve_entities")
def resolve_entities(self, job_id: Optional[int] = None):
    """Run graph-based entity resolution and update lead-building relationships."""
    session = _get_pg_session()

    try:
        from src.tasks.ingest import _ensure_or_create_job, _finish_job
        job_id = _ensure_or_create_job(session, job_id, "entity_resolution", "leads")
        session.commit()

        G = _build_entity_graph(session)
        clusters = _resolve_clusters(G)

        updated = 0
        for cluster in clusters:
            if cluster["portfolio_size"] < 1:
                continue

            canonical = cluster["canonical_name"]

            result = session.execute(
                text("SELECT lead_id FROM leads WHERE company_name = :name OR owner_name = :name LIMIT 1"),
                {"name": canonical},
            ).first()

            if result:
                lead_id = result[0]
                session.execute(
                    text("""
                        UPDATE leads SET
                            portfolio_size = :size,
                            updated_at = now()
                        WHERE lead_id = :lid
                    """),
                    {
                        "lid": lead_id,
                        "size": cluster["portfolio_size"],
                    },
                )

                for bbl in cluster["bbls"]:
                    session.execute(
                        text("""
                            INSERT INTO building_management (bbl, lead_id, role, is_current, created_at, updated_at)
                            SELECT :bbl, :lid, 'agent', true, now(), now()
                            WHERE NOT EXISTS (
                                SELECT 1 FROM building_management
                                WHERE bbl = :bbl AND lead_id = :lid AND is_current = true
                            )
                        """),
                        {"bbl": bbl, "lid": lead_id},
                    )

                updated += 1
                if updated % 100 == 0:
                    session.commit()

        session.commit()
        _finish_job(session, job_id, "completed", len(clusters), updated, 0)
        session.commit()
        logger.info(f"Entity resolution: {len(clusters)} clusters, {updated} leads updated")
        return {"job_id": job_id, "clusters": len(clusters), "updated": updated}

    except Exception as e:
        logger.error(f"Entity resolution failed: {e}", exc_info=True)
        try:
            from src.tasks.ingest import _finish_job
            if job_id is not None:
                _finish_job(session, job_id, "failed", 0, 0, 1, str(e)[:500])
                session.commit()
        except Exception:
            session.rollback()
        session.rollback()
        session.close()
        raise
