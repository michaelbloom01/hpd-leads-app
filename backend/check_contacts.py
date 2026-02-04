import requests

# Count contact types
r = requests.get('https://data.cityofnewyork.us/resource/feu5-w2e2.json', 
    params={'$select': 'type, count(*)', '$group': 'type'})

print('=== CONTACT TYPES IN HPD DATABASE ===')
for row in r.json():
    print(f"{row['type']:20} : {int(row['count']):,} records")

# Check how Agent type looks (this is the management company)
print('\n=== SAMPLE AGENTS (Management Companies) ===')
r = requests.get('https://data.cityofnewyork.us/resource/feu5-w2e2.json', 
    params={'$where': "type='Agent'", '$limit': 20})
for c in r.json():
    name = c.get('corporationname', '') or f"{c.get('firstname', '')} {c.get('lastname', '')}".strip()
    addr = f"{c.get('businesshousenumber', '')} {c.get('businessstreetname', '')}, {c.get('businesscity', '')} {c.get('businessstate', '')} {c.get('businesszip', '')}"
    print(f"{name[:40]:40} | {addr[:50]}")
