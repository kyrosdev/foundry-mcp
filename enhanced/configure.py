"""Generate a Claude MCP entry for this package location; does not install it."""
import argparse
import json
from pathlib import Path
import sys

def main():
    root=Path(__file__).resolve().parent
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--workspace',default=str(root.parent))
    parser.add_argument('--label',default='Claude Desktop')
    args=parser.parse_args()
    workspace=Path(args.workspace).resolve()
    if not workspace.is_dir(): parser.error('Workspace folder does not exist')
    config={'mcpServers':{'Foundry_MCP':{'command':sys.executable,
        'args':[str(root/'server.py'),'--workspace',str(workspace),'--session-label',args.label]}}}
    output=root/'claude-config.json'
    output.write_text(json.dumps(config,indent=2),encoding='utf-8')
    print(str(output))

if __name__=='__main__':main()
