import json
import sys
from pathlib import Path


def main() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    sys.path.insert(0, str(repo_root))
    from backend.main import app

    schema = app.openapi()
    out_path = repo_root / "openapi.json"
    out_path.write_text(json.dumps(schema, indent=2) + "\n")
    print(f"[export_openapi] wrote {out_path} ({len(schema.get('paths', {}))} paths)")


if __name__ == "__main__":
    main()
