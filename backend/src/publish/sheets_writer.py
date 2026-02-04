"""
Google Sheets writer for lead output.

See docs/05-google-sheet-design.md for sheet structure.
"""
import logging
from datetime import datetime
from pathlib import Path
from typing import List, Tuple, Optional, Dict, Any

from config.settings import settings
from src.transform.aggregate import Lead

logger = logging.getLogger(__name__)

# Column headers for the Leads sheet
HEADERS = [
    "Score", "Tier", "Agent Name", "Owner Name", "Portfolio Size", "Total Units",
    "Phone", "Email", "Website", "Business Summary", "Owner/Principal",
    "Address", "Primary Boro", "All Boros", "Tags", "Enrichment Status",
    "Last Updated", "Opportunity Notes", "Status", "Lead ID"
]

# Manual columns that should be preserved on updates (0-indexed)
MANUAL_COLUMNS = [17, 18]  # Opportunity Notes, Status

SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]


class SheetsWriter:
    """Write leads to Google Sheets."""
    
    def __init__(self, sheet_id: Optional[str] = None, creds_path: Optional[str] = None):
        """
        Initialize the Sheets writer.
        
        Args:
            sheet_id: Google Sheet ID (from URL)
            creds_path: Path to service account credentials JSON
        """
        self.sheet_id = sheet_id or settings.google_sheet_id
        self.creds_path = creds_path or settings.google_sheets_credentials
        self._service = None
    
    @property
    def service(self):
        """Lazy-load the Sheets API service."""
        if self._service is None:
            try:
                from google.oauth2.service_account import Credentials
                from googleapiclient.discovery import build
                
                creds = Credentials.from_service_account_file(
                    self.creds_path, scopes=SCOPES
                )
                self._service = build("sheets", "v4", credentials=creds)
            except Exception as e:
                logger.error(f"Failed to initialize Sheets API: {e}")
                raise
        return self._service
    
    def full_refresh(self, leads: List[Lead]) -> int:
        """
        Clear and rewrite all lead data, preserving manual columns.
        
        Args:
            leads: All leads to write
            
        Returns:
            Number of rows written
        """
        if not self.sheet_id:
            raise ValueError("Google Sheet ID not configured")
        
        logger.info(f"Full refresh: writing {len(leads)} leads")
        
        # Read existing data to preserve manual columns
        existing_manual = self._read_manual_columns()
        
        # Clear the data range (keep headers)
        self._clear_data()
        
        # Convert leads to rows
        rows = [self._lead_to_row(lead) for lead in leads]
        
        # Restore manual columns where lead_id matches
        for row in rows:
            lead_id = row[-1]  # Last column is lead_id
            if lead_id in existing_manual:
                manual_data = existing_manual[lead_id]
                row[17] = manual_data.get("notes", "")  # Opportunity Notes
                row[18] = manual_data.get("status", "")  # Status
        
        # Write headers + data
        all_rows = [HEADERS] + rows
        self._write_range("Leads!A1", all_rows)
        
        # Update metadata
        self._update_metadata(len(leads))
        
        logger.info(f"Wrote {len(rows)} rows to Google Sheet")
        return len(rows)
    
    def incremental_update(self, leads: List[Lead]) -> Tuple[int, int]:
        """
        Update existing rows and append new ones.
        
        Args:
            leads: Leads to upsert
            
        Returns:
            Tuple of (updated_count, added_count)
        """
        if not self.sheet_id:
            raise ValueError("Google Sheet ID not configured")
        
        logger.info(f"Incremental update: processing {len(leads)} leads")
        
        # Read existing lead IDs and their row numbers
        existing = self._read_existing_leads()
        
        updated = 0
        added = 0
        new_rows = []
        
        for lead in leads:
            row = self._lead_to_row(lead)
            
            if lead.lead_id in existing:
                # Update existing row
                row_num = existing[lead.lead_id]["row"]
                # Preserve manual columns
                row[17] = existing[lead.lead_id].get("notes", "")
                row[18] = existing[lead.lead_id].get("status", "")
                
                self._write_range(f"Leads!A{row_num}", [row])
                updated += 1
            else:
                # Queue for append
                new_rows.append(row)
        
        # Append all new rows at once
        if new_rows:
            self._append_rows("Leads", new_rows)
            added = len(new_rows)
        
        # Update metadata
        total = len(existing) + added
        self._update_metadata(total)
        
        logger.info(f"Updated {updated}, added {added} leads")
        return updated, added
    
    def _lead_to_row(self, lead: Lead) -> List[Any]:
        """Convert a Lead to a sheet row."""
        return [
            lead.score,
            self._get_tier(lead.score),
            lead.agent_name or "",
            lead.owner_name or "",
            lead.portfolio_size,
            lead.total_units,
            lead.phone or "",
            lead.email or "",
            lead.website or "",
            lead.business_summary or "",
            lead.owner_principal or "",
            lead.address or "",
            lead.boro or "",
            ", ".join(lead.boros) if lead.boros else "",
            ", ".join(lead.tags) if lead.tags else "",
            lead.enrichment_status,
            lead.updated_at.strftime("%Y-%m-%d") if lead.updated_at else datetime.now().strftime("%Y-%m-%d"),
            lead.opportunity_note or "",
            "",  # Status (manual column)
            lead.lead_id,
        ]
    
    def _get_tier(self, score: float) -> str:
        """Get tier letter from score."""
        if score >= 80:
            return "A"
        elif score >= 60:
            return "B"
        elif score >= 40:
            return "C"
        elif score >= 20:
            return "D"
        else:
            return "F"
    
    def _read_manual_columns(self) -> Dict[str, Dict[str, str]]:
        """
        Read existing manual columns (Notes, Status) by lead_id.
        
        Returns:
            Dict mapping lead_id to {notes, status}
        """
        try:
            # Read columns R (notes), S (status), T (lead_id)
            result = self.service.spreadsheets().values().get(
                spreadsheetId=self.sheet_id,
                range="Leads!R2:T"  # Skip header
            ).execute()
            
            rows = result.get("values", [])
            manual = {}
            for row in rows:
                if len(row) >= 3:
                    notes = row[0] if len(row) > 0 else ""
                    status = row[1] if len(row) > 1 else ""
                    lead_id = row[2] if len(row) > 2 else ""
                    if lead_id:
                        manual[lead_id] = {"notes": notes, "status": status}
            
            logger.debug(f"Read {len(manual)} existing manual entries")
            return manual
        except Exception as e:
            logger.warning(f"Could not read existing manual columns: {e}")
            return {}
    
    def _read_existing_leads(self) -> Dict[str, Dict]:
        """
        Read existing leads with their row numbers.
        
        Returns:
            Dict mapping lead_id to {row, notes, status}
        """
        try:
            # Read all data
            result = self.service.spreadsheets().values().get(
                spreadsheetId=self.sheet_id,
                range="Leads!A2:T"  # Skip header
            ).execute()
            
            rows = result.get("values", [])
            existing = {}
            for i, row in enumerate(rows):
                row_num = i + 2  # Account for header and 1-based indexing
                if len(row) >= 20:  # Full row
                    lead_id = row[19]  # Column T (0-indexed: 19)
                    notes = row[17] if len(row) > 17 else ""
                    status = row[18] if len(row) > 18 else ""
                    if lead_id:
                        existing[lead_id] = {
                            "row": row_num,
                            "notes": notes,
                            "status": status
                        }
            
            logger.debug(f"Read {len(existing)} existing leads")
            return existing
        except Exception as e:
            logger.warning(f"Could not read existing leads: {e}")
            return {}
    
    def _clear_data(self):
        """Clear all data rows (keep headers)."""
        try:
            self.service.spreadsheets().values().clear(
                spreadsheetId=self.sheet_id,
                range="Leads!A2:T"
            ).execute()
            logger.debug("Cleared existing data")
        except Exception as e:
            logger.warning(f"Could not clear data: {e}")
    
    def _write_range(self, range_name: str, values: List[List]):
        """Write values to a specific range."""
        body = {"values": values}
        self.service.spreadsheets().values().update(
            spreadsheetId=self.sheet_id,
            range=range_name,
            valueInputOption="USER_ENTERED",
            body=body
        ).execute()
    
    def _append_rows(self, sheet_name: str, rows: List[List]):
        """Append rows to a sheet."""
        body = {"values": rows}
        self.service.spreadsheets().values().append(
            spreadsheetId=self.sheet_id,
            range=f"{sheet_name}!A:T",
            valueInputOption="USER_ENTERED",
            insertDataOption="INSERT_ROWS",
            body=body
        ).execute()
    
    def _update_metadata(self, total_leads: int):
        """Update the Config tab with metadata."""
        try:
            metadata = [
                ["Last Refresh", datetime.now().strftime("%Y-%m-%d %H:%M:%S")],
                ["Total Leads", total_leads],
            ]
            self._write_range("Config!A1", metadata)
        except Exception as e:
            logger.warning(f"Could not update metadata: {e}")
    
    def export_to_csv(self, leads: List[Lead], output_path: str) -> str:
        """
        Export leads to CSV file (for backup or offline use).
        
        Args:
            leads: Leads to export
            output_path: Path to output CSV file
            
        Returns:
            Path to created file
        """
        import csv
        
        output = Path(output_path)
        output.parent.mkdir(parents=True, exist_ok=True)
        
        with open(output, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(HEADERS)
            for lead in leads:
                writer.writerow(self._lead_to_row(lead))
        
        logger.info(f"Exported {len(leads)} leads to {output}")
        return str(output)
