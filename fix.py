import re

with open(r'd:\RevertX\primary_agent\procurement_agent.py', 'r', encoding='utf-8') as f:
    content = f.read()

content = content.replace('f"{PROXY_URL}", headers=HEADERS/init_workflow"', 'f"{PROXY_URL}/init_workflow", headers=HEADERS')
content = content.replace('f"{PROXY_URL}", headers=HEADERS/pay"', 'f"{PROXY_URL}/pay", headers=HEADERS')
content = content.replace('f"{PROXY_URL}", headers=HEADERS/workflow/{wid}"', 'f"{PROXY_URL}/workflow/{wid}", headers=HEADERS')

with open(r'd:\RevertX\primary_agent\procurement_agent.py', 'w', encoding='utf-8') as f:
    f.write(content)
