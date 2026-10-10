import sys
from pathlib import Path

import yaml

from geonames_server.api.app import app

spec = yaml.safe_dump(app.openapi(), sort_keys=False, allow_unicode=False)
output_path = Path("/workspace/openapi.yaml")
if "--check" in sys.argv:
    if output_path.read_text(encoding="utf-8") != spec:
        print(f"openapi-check: {output_path} is stale, run make openapi")
        sys.exit(1)
else:
    output_path.write_text(spec, encoding="utf-8")
    print(f"Wrote {output_path}")
