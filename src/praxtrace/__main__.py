import importlib
import sys
COMMANDS = {"check": "execution_record", "cases": "case_records", "ops": "case_ops", "upstream": "product_upstream", "usage": "upstream_usage", "citations": "citation_check", "inventory": "source_inventory", "rules": "rule_lookup", "periods": "compare_periods", "routes": "route_map"}
def main():
    if len(sys.argv) == 1 or sys.argv[1] in ("-h", "--help"):
        print("PraxTrace: business evidence and continuous verification for AI agents")
        print("Commands: " + ", ".join(COMMANDS) + ", guide, version")
        return 0
    command = sys.argv.pop(1)
    if command == "version":
        from . import __version__
        print(__version__); return 0
    if command == "guide":
        from pathlib import Path
        print(Path(__file__).with_name("AGENT_GUIDE.md").read_text(encoding="utf-8")); return 0
    if command not in COMMANDS:
        print("Unknown command: " + command, file=sys.stderr); return 2
    return importlib.import_module("praxtrace." + COMMANDS[command]).main()
if __name__ == "__main__":
    raise SystemExit(main())
