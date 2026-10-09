from pathlib import Path

import yaml

from geonames_server.api.app import app

spec = app.openapi()
output_path = Path("/workspace/openapi.yaml")
output_path.write_text(
    yaml.safe_dump(spec, sort_keys=False, allow_unicode=False), encoding="utf-8"
)
print(f"Wrote {output_path}")
