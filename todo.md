1. make ingestion faster
2. migrate to uv
4. better error handling including keyboard interrupts
5. better rich console printing and integration (check google gemini)
6. create a copier template with database, downloader, http requests, dev container, python and uv (with everything nicely setup)
7. github ci/cd workflow
8. write another project to download deletes-yyyy-mm-dd.txt and modifications-yyyy-mm-dd.txt into a repo and integrate it here to sync the data
9. docker compose for users
10. add geospacial capabilities, nearby, nearby lakes, etc
11. add rate limiting, api key support
12. deploy it commercially
13. add country dataset support
14. add postal code dataset support
15. make openapi.json and openapi.yaml specs - use scripts/openapi.py to generate it
16. make a bruno export file for testing the api
17. use redis
18. single source of truth for app name and version from pyproject.toml

update ingestion

Nice—this is exactly the direction you want if you’re aiming for a **production-grade ingestion pipeline**. I’ll break this into three parts:

1. ✅ Chunked streaming with COPY (memory-safe, configurable)
2. 💀 Dead-letter queue explained (and implemented)
3. 📦 Turning this into a reusable ingestion framework package

---

# 1️⃣ Chunked streaming + flexible file parsing

Instead of loading everything into **pandas**, we stream:

* Read file in chunks (`chunksize`)
* Validate + transform chunk
* Stream into PostgreSQL `COPY`
* Merge per chunk

---

## 🔧 Improved Generic Ingestor (core ideas)

```python id="chunked_ingestor_v1"
import io
from pathlib import Path
from typing import Type, Callable, Any, Iterable

import pandas as pd
import psycopg2
from psycopg2 import sql
from pydantic import BaseModel, ValidationError
from sqlalchemy.orm import DeclarativeMeta


class ChunkedIngestor:
    def __init__(
        self,
        conn_info: dict,
        chunk_size: int = 10_000,
        separator: str = "\t",
        has_header: bool = True,
        encoding: str = "utf-8",
    ):
        self.conn_params = conn_info
        self.chunk_size = chunk_size
        self.separator = separator
        self.has_header = has_header
        self.encoding = encoding

    # -------------------------
    # STREAM FILE IN CHUNKS
    # -------------------------
    def _read_chunks(self, path: Path) -> Iterable[pd.DataFrame]:
        return pd.read_csv(
            path,
            sep=self.separator,
            chunksize=self.chunk_size,
            header=0 if self.has_header else None,
            dtype=str,
            encoding=self.encoding,
            keep_default_na=False,
        )

    # -------------------------
    # VALIDATE + SPLIT GOOD/BAD
    # -------------------------
    def _validate_chunk(
        self,
        df: pd.DataFrame,
        model: Type[BaseModel],
    ):
        valid_rows = []
        invalid_rows = []

        for record in df.to_dict(orient="records"):
            try:
                obj = model(**record)
                valid_rows.append(obj)
            except ValidationError as e:
                invalid_rows.append(
                    {
                        "raw": record,
                        "error": str(e),
                    }
                )

        return valid_rows, invalid_rows

    # -------------------------
    # MAP TO TARGET
    # -------------------------
    def _map_rows(
        self,
        rows: list[BaseModel],
        field_mapping: dict[str, str],
        computed_fields: dict[str, Callable[[BaseModel], Any]] | None,
    ) -> pd.DataFrame:
        output = []

        for r in rows:
            data = r.model_dump()
            row = {}

            for src, dest in field_mapping.items():
                row[dest] = data.get(src)

            if computed_fields:
                for k, fn in computed_fields.items():
                    row[k] = fn(r)

            output.append(row)

        return pd.DataFrame(output)

    # -------------------------
    # COPY CHUNK
    # -------------------------
    def _copy_chunk(
        self,
        cur,
        df: pd.DataFrame,
        temp_table: str,
    ):
        if df.empty:
            return

        buffer = io.StringIO()
        df.to_csv(buffer, sep="\t", header=False, index=False, na_rep="")
        buffer.seek(0)

        cols = list(df.columns)

        copy_sql = sql.SQL(
            "COPY {temp} ({cols}) FROM STDIN WITH (FORMAT csv, DELIMITER E'\\t', NULL '')"
        ).format(
            temp=sql.Identifier(temp_table),
            cols=sql.SQL(", ").join(map(sql.Identifier, cols)),
        )

        cur.copy_expert(copy_sql, buffer)

    # -------------------------
    # MAIN INGEST
    # -------------------------
    def ingest(
        self,
        file_path: str | Path,
        pydantic_model: Type[BaseModel],
        sqlalchemy_model: Type[DeclarativeMeta],
        field_mapping: dict[str, str],
        conflict_columns: list[str],
        computed_fields: dict[str, Callable] | None = None,
        dead_letter_path: str | None = None,
    ):
        path = Path(file_path)
        table = sqlalchemy_model.__table__
        temp_table = f"tmp_{table.name}_stage"

        dead_letter = []

        with psycopg2.connect(**self.conn_params) as conn:
            with conn.cursor() as cur:
                # create temp table once
                cur.execute(
                    sql.SQL(
                        "CREATE TEMP TABLE {temp} (LIKE {target} INCLUDING DEFAULTS) ON COMMIT DROP"
                    ).format(
                        temp=sql.Identifier(temp_table),
                        target=sql.Identifier(table.name),
                    )
                )

                for chunk in self._read_chunks(path):
                    valid, invalid = self._validate_chunk(chunk, pydantic_model)

                    dead_letter.extend(invalid)

                    mapped_df = self._map_rows(valid, field_mapping, computed_fields)

                    self._copy_chunk(cur, mapped_df, temp_table)

                # merge once at end
                self._merge(
                    cur,
                    temp_table,
                    table.name,
                    list(mapped_df.columns),
                    conflict_columns,
                )

        # write dead letter file
        if dead_letter_path and dead_letter:
            pd.DataFrame(dead_letter).to_json(
                dead_letter_path, lines=True, orient="records"
            )
```

---

# 2️⃣ 💀 What is a Dead-Letter Queue?

A **dead-letter queue (DLQ)** is:

> A place where **bad / unprocessable records go instead of crashing ingestion**

---

## 🔥 Why it matters

Without DLQ:

* One bad row → whole pipeline fails ❌

With DLQ:

* Good rows → inserted ✅
* Bad rows → saved for later inspection 🔍

---

## 🧾 Example DLQ record

```json id="dlq_example"
{
  "raw": {"geonameid": "abc", "latitude": "not_a_float"},
  "error": "value is not a valid float"
}
```

---

## 🧠 In practice

You can:

* Store as JSON file ✅ (simple)
* Send to Kafka / SQS 🚀
* Store in a DB table 🧱

---

# 3️⃣ 📦 Turning this into a reusable ingestion framework

Here’s a clean package structure:

```
ingestion_framework/
│
├── core/
│   ├── ingestor.py
│   ├── chunking.py
│   ├── validation.py
│   ├── mapping.py
│   ├── copy.py
│   └── merge.py
│
├── config/
│   └── settings.py
│
├── interfaces/
│   └── schemas.py   # base Pydantic models
│
├── utils/
│   ├── logging.py
│   └── dlq.py
│
├── examples/
│   └── geonames_ingestion.py
│
├── __init__.py
└── pyproject.toml
```

---

## 🔌 Clean public API

```python id="framework_api"
from ingestion_framework import Ingestor

ingestor = Ingestor(config)

ingestor.run(
    file_path="data.txt",
    input_model=GeoNameIn,
    db_model=GeoName,
    mapping=field_mapping,
    computed=computed_fields,
    conflict_keys=["id"],
)
```

---

## 🧩 Extensibility hooks

Add pluggable components:

```python id="hooks_example"
class BaseTransformer:
    def transform(self, row): ...


class BaseValidator:
    def validate(self, row): ...
```

---

## 🚀 Future upgrades (real-world level)

* Parallel chunk ingestion (multiprocessing)
* Backpressure handling
* Schema evolution handling
* Metrics (Prometheus)
* Retry logic
* Idempotency keys

---

# ⚡ Key takeaway

You now have:

* ✅ Streaming ingestion (scales to GBs)
* ✅ Schema validation via **Pydantic**
* ✅ Fast bulk load via PostgreSQL COPY
* ✅ Safe failure handling (DLQ)
* ✅ Clean abstraction for reuse

---

If you want, I can next:

* Add **parallel ingestion with worker pools**
* Add **Airflow-compatible operator**
* Or turn this into a **pip-installable package with CLI (`ingest run config.yaml`)**


Fair point — structure without content isn’t that useful. Let’s turn this into a **real, usable mini-framework**, with clear responsibilities for each file.

I’ll keep it practical: minimal abstraction, but clean separation.

---

# 📦 Package: `ingestion_framework`

---

# 🔹 `core/ingestor.py` (main orchestrator)

This is what users call.

```python id="core_ingestor"
from pathlib import Path
from typing import Type

from sqlalchemy.orm import DeclarativeMeta
from pydantic import BaseModel

from ingestion_framework.core.chunking import read_chunks
from ingestion_framework.core.validation import validate_chunk
from ingestion_framework.core.mapping import map_rows
from ingestion_framework.core.copy import copy_dataframe
from ingestion_framework.core.merge import merge_tables
from ingestion_framework.utils.dlq import DeadLetterQueue


class Ingestor:
    def __init__(self, conn_params: dict, config):
        self.conn_params = conn_params
        self.config = config

    def run(
        self,
        file_path: str,
        input_model: Type[BaseModel],
        db_model: Type[DeclarativeMeta],
        mapping: dict[str, str],
        conflict_keys: list[str],
        computed: dict | None = None,
        dlq_path: str | None = None,
    ):
        import psycopg2
        from psycopg2 import sql

        table = db_model.__table__
        temp_table = f"tmp_{table.name}_stage"

        dlq = DeadLetterQueue(dlq_path)

        with psycopg2.connect(**self.conn_params) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    sql.SQL(
                        "CREATE TEMP TABLE {temp} (LIKE {target} INCLUDING DEFAULTS) ON COMMIT DROP"
                    ).format(
                        temp=sql.Identifier(temp_table),
                        target=sql.Identifier(table.name),
                    )
                )

                last_columns = None

                for chunk in read_chunks(Path(file_path), self.config):
                    valid, invalid = validate_chunk(chunk, input_model)

                    dlq.add(invalid)

                    df = map_rows(valid, mapping, computed)
                    if not df.empty:
                        last_columns = list(df.columns)
                        copy_dataframe(cur, df, temp_table)

                if last_columns:
                    merge_tables(
                        cur,
                        temp_table,
                        table.name,
                        last_columns,
                        conflict_keys,
                    )

        dlq.flush()
```

---

# 🔹 `core/chunking.py`

```python id="chunking"
import pandas as pd
from pathlib import Path


def read_chunks(path: Path, config):
    return pd.read_csv(
        path,
        sep=config.separator,
        chunksize=config.chunk_size,
        header=0 if config.has_header else None,
        dtype=str,
        encoding=config.encoding,
        keep_default_na=False,
    )
```

---

# 🔹 `core/validation.py`

Uses **Pydantic**

```python id="validation"
from typing import Type
from pydantic import BaseModel, ValidationError


def validate_chunk(df, model: Type[BaseModel]):
    valid = []
    invalid = []

    for record in df.to_dict(orient="records"):
        try:
            obj = model(**record)
            valid.append(obj)
        except ValidationError as e:
            invalid.append(
                {
                    "raw": record,
                    "error": str(e),
                }
            )

    return valid, invalid
```

---

# 🔹 `core/mapping.py`

```python id="mapping"
from typing import Callable, Any
import pandas as pd


def map_rows(
    rows,
    field_mapping: dict[str, str],
    computed_fields: dict[str, Callable] | None = None,
):
    output = []

    for r in rows:
        data = r.model_dump()
        row = {}

        for src, dest in field_mapping.items():
            row[dest] = data.get(src)

        if computed_fields:
            for k, fn in computed_fields.items():
                row[k] = fn(r)

        output.append(row)

    return pd.DataFrame(output)
```

---

# 🔹 `core/copy.py`

Uses PostgreSQL COPY for speed.

```python id="copy"
import io
from psycopg2 import sql


def copy_dataframe(cur, df, temp_table: str):
    if df.empty:
        return

    buffer = io.StringIO()
    df.to_csv(buffer, sep="\t", header=False, index=False, na_rep="")
    buffer.seek(0)

    columns = list(df.columns)

    copy_sql = sql.SQL(
        "COPY {table} ({cols}) FROM STDIN WITH (FORMAT csv, DELIMITER E'\\t', NULL '')"
    ).format(
        table=sql.Identifier(temp_table),
        cols=sql.SQL(", ").join(map(sql.Identifier, columns)),
    )

    cur.copy_expert(copy_sql, buffer)
```

---

# 🔹 `core/merge.py`

```python id="merge"
from psycopg2 import sql


def merge_tables(
    cur,
    temp_table: str,
    target_table: str,
    columns: list[str],
    conflict_columns: list[str],
):
    update_columns = [c for c in columns if c not in conflict_columns]

    if update_columns:
        updates = sql.SQL(", ").join(
            sql.SQL("{c}=EXCLUDED.{c}").format(c=sql.Identifier(c))
            for c in update_columns
        )
        action = sql.SQL("DO UPDATE SET {updates}").format(updates=updates)
    else:
        action = sql.SQL("DO NOTHING")

    query = sql.SQL("""
        INSERT INTO {target} ({cols})
        SELECT {cols} FROM {temp}
        ON CONFLICT ({conflict})
        {action}
    """).format(
        target=sql.Identifier(target_table),
        temp=sql.Identifier(temp_table),
        cols=sql.SQL(", ").join(map(sql.Identifier, columns)),
        conflict=sql.SQL(", ").join(map(sql.Identifier, conflict_columns)),
        action=action,
    )

    cur.execute(query)
```

---

# 🔹 `config/settings.py`

```python id="config"
class IngestionConfig:
    def __init__(
        self,
        chunk_size: int = 10000,
        separator: str = "\t",
        has_header: bool = True,
        encoding: str = "utf-8",
    ):
        self.chunk_size = chunk_size
        self.separator = separator
        self.has_header = has_header
        self.encoding = encoding
```

---

# 🔹 `utils/dlq.py` (Dead-letter queue)

```python id="dlq"
import json


class DeadLetterQueue:
    def __init__(self, path: str | None):
        self.path = path
        self.records = []

    def add(self, rows):
        self.records.extend(rows)

    def flush(self):
        if not self.path or not self.records:
            return

        with open(self.path, "w") as f:
            for r in self.records:
                f.write(json.dumps(r) + "\n")
```

---

# 🔹 `interfaces/schemas.py` (optional base classes)

```python id="schemas"
from pydantic import BaseModel


class BaseIngestModel(BaseModel):
    class Config:
        extra = "ignore"  # ignore unexpected columns
```

---

# 🔹 `__init__.py`

```python id="init"
from ingestion_framework.core.ingestor import Ingestor
from ingestion_framework.config.settings import IngestionConfig

__all__ = ["Ingestor", "IngestionConfig"]
```

---

# 🔥 Example usage (end-to-end)

```python id="example_usage"
from ingestion_framework import Ingestor, IngestionConfig


config = IngestionConfig(chunk_size=5000, separator="\t")

ingestor = Ingestor(conn_params, config)

ingestor.run(
    file_path="data.txt",
    input_model=GeoNameIn,
    db_model=GeoName,
    mapping={
        "geonameid": "id",
        "name": "name",
    },
    computed={"slug": lambda r: r.name.lower().replace(" ", "-")},
    conflict_keys=["id"],
    dlq_path="bad_rows.jsonl",
)
```

---

# 🧠 What you now have

This is no longer just a script — it’s a **real ingestion framework**:

* Modular (swap any layer)
* Memory-safe (chunked)
* Fast (COPY)
* Safe (DLQ)
* Flexible (mapping + computed fields)

---

If you want to push this one level further, I can help you add:

* CLI (`ingest run config.yaml`)
* Async ingestion
* Parallel chunk workers
* Airflow / Dagster integration

Just tell me 👍
Absolutely! What you’re describing is a very common scenario in production: the **Pydantic input model** may not match the **SQLAlchemy table** 1:1. Some columns exist in only one of them, order may differ, and you may have optional or computed fields.

We can improve the mapping in a few ways:

---

## 1️⃣ Use a **field mapping dictionary** (explicit, safe)

You already have this:

```python
field_mapping = {
    "geonameid": "id",
    "name": "name",
}
```

Pros: explicit and safe.
Cons: you have to manually map every field, which is cumbersome if many fields match by name.

---

## 2️⃣ Use **automatic matching by name**

If your Pydantic and SQLAlchemy fields have the same names (or close), you can map automatically:

```python
from typing import Dict
from sqlalchemy.orm import DeclarativeMeta


def auto_map_fields(
    pydantic_model: type, sqlalchemy_model: DeclarativeMeta
) -> Dict[str, str]:
    # Get SQLAlchemy column names
    sa_columns = {c.name for c in sqlalchemy_model.__table__.columns}

    # Get Pydantic field names
    pydantic_fields = set(pydantic_model.model_fields.keys())

    # Auto-map fields with matching names
    return {field: field for field in pydantic_fields if field in sa_columns}
```

This way, you don’t have to write a mapping for every column that is identical.

---

## 3️⃣ Support **partial or fuzzy mapping**

Sometimes field names differ slightly:

* `geonameid` → `id`
* `lat` → `latitude`

You can combine **explicit mapping + automatic mapping**:

```python
def build_mapping(
    pydantic_model: type,
    sqlalchemy_model: DeclarativeMeta,
    explicit_mapping: dict[str, str] | None = None,
) -> dict[str, str]:
    explicit_mapping = explicit_mapping or {}

    # auto-map the rest
    sa_columns = {c.name for c in sqlalchemy_model.__table__.columns}
    pyd_fields = set(pydantic_model.model_fields.keys())

    auto_mapped = {
        f: f for f in pyd_fields if f in sa_columns and f not in explicit_mapping
    }

    # combine explicit and auto-mapped
    final_mapping = {**auto_mapped, **explicit_mapping}
    return final_mapping
```

Usage:

```python
mapping = build_mapping(
    GeoNameIn, GeoName, explicit_mapping={"geonameid": "id", "lat": "latitude"}
)
```

This will:

* Map `geonameid → id` (explicit)
* Map `lat → latitude` (explicit)
* Map any other identical fields automatically

---

## 4️⃣ Handle **extra SQLAlchemy columns**

Some SQLAlchemy columns may not exist in the Pydantic model (e.g., `created_at`, `updated_at`). For those:

* Keep them nullable or provide defaults in SQLAlchemy
* Or compute them dynamically in `computed_fields`:

```python
computed_fields = {
    "created_at": lambda _: datetime.utcnow(),
}
```

---

## 5️⃣ Full generic mapping strategy

```python
def map_pydantic_to_sqlalchemy(
    row,  # Pydantic model instance
    field_mapping: dict[str, str],
    computed_fields: dict[str, Callable] | None = None,
):
    data = row.model_dump()
    mapped_row = {field_mapping[src]: data[src] for src in field_mapping if src in data}

    if computed_fields:
        for field, func in computed_fields.items():
            mapped_row[field] = func(row)

    return mapped_row
```

✅ Benefits:

* Works even if Pydantic model has fewer fields than SQLAlchemy
* Works if fields are out of order
* Automatically applies computed fields
* Flexible for partial mapping

---

### Suggested workflow in your ingestion framework

1. Build **combined mapping**: explicit + automatic
2. Apply `map_pydantic_to_sqlalchemy()` to each validated row
3. Convert list of mapped rows to DataFrame for COPY
4. Use computed fields for derived columns or default values

This removes the need for exact column ordering or complete one-to-one mapping.

---

If you want, I can **rewrite your chunked ingestion framework** to **automatically handle Pydantic → SQLAlchemy mapping** including:

* Automatic name matching
* Explicit overrides
* Computed fields
* Extra SQLAlchemy columns

So that the user only has to define a Pydantic model and optionally a small mapping + computed fields.

Do you want me to do that?

Perfect! Let’s upgrade your **chunked ingestion framework** to fully handle **Pydantic → SQLAlchemy mapping** automatically, while keeping explicit overrides and computed fields. This will make the ingestion nearly plug-and-play.

Here’s a **refactored version** of your ingestion framework.

---

# 🔹 Updated `core/mapping.py` – automatic and explicit mapping

```python
from typing import Callable, Any
import pandas as pd


def build_mapping(
    pydantic_model: type,
    sqlalchemy_model,
    explicit_mapping: dict[str, str] | None = None,
) -> dict[str, str]:
    """
    Combine explicit mapping and automatic matching of Pydantic -> SQLAlchemy fields
    """
    explicit_mapping = explicit_mapping or {}

    # SQLAlchemy table columns
    sa_columns = {c.name for c in sqlalchemy_model.__table__.columns}

    # Pydantic model fields
    pyd_fields = set(pydantic_model.model_fields.keys())

    # auto-map fields with matching names
    auto_mapped = {
        f: f for f in pyd_fields if f in sa_columns and f not in explicit_mapping
    }

    # combine explicit + automatic
    return {**auto_mapped, **explicit_mapping}


def map_pydantic_to_sqlalchemy(
    row,  # Pydantic model instance
    field_mapping: dict[str, str],
    computed_fields: dict[str, Callable] | None = None,
):
    """
    Convert one Pydantic instance to a dict ready for SQLAlchemy/COPY
    """
    data = row.model_dump()
    mapped_row = {field_mapping[src]: data[src] for src in field_mapping if src in data}

    if computed_fields:
        for field, func in computed_fields.items():
            mapped_row[field] = func(row)

    return mapped_row


def map_rows(
    rows,
    field_mapping: dict[str, str],
    computed_fields: dict[str, Callable] | None = None,
) -> pd.DataFrame:
    mapped = [
        map_pydantic_to_sqlalchemy(r, field_mapping, computed_fields) for r in rows
    ]
    return pd.DataFrame(mapped)
```

---

# 🔹 Updated `core/ingestor.py` – fully generic mapping

```python
from pathlib import Path
from typing import Type

from sqlalchemy.orm import DeclarativeMeta
from pydantic import BaseModel

from ingestion_framework.core.chunking import read_chunks
from ingestion_framework.core.validation import validate_chunk
from ingestion_framework.core.mapping import map_rows, build_mapping
from ingestion_framework.core.copy import copy_dataframe
from ingestion_framework.core.merge import merge_tables
from ingestion_framework.utils.dlq import DeadLetterQueue


class Ingestor:
    def __init__(self, conn_params: dict, config):
        self.conn_params = conn_params
        self.config = config

    def run(
        self,
        file_path: str,
        input_model: Type[BaseModel],
        db_model: Type[DeclarativeMeta],
        explicit_mapping: dict[str, str] | None = None,
        conflict_keys: list[str] | None = None,
        computed: dict | None = None,
        dlq_path: str | None = None,
    ):
        import psycopg2
        from psycopg2 import sql

        path = Path(file_path)
        table = db_model.__table__
        temp_table = f"tmp_{table.name}_stage"

        # Build combined field mapping
        field_mapping = build_mapping(input_model, db_model, explicit_mapping)

        # Determine conflict keys if not provided
        if conflict_keys is None:
            conflict_keys = [c.name for c in table.primary_key.columns]

        dlq = DeadLetterQueue(dlq_path)

        with psycopg2.connect(**self.conn_params) as conn:
            with conn.cursor() as cur:
                # Create staging table once
                cur.execute(
                    sql.SQL(
                        "CREATE TEMP TABLE {temp} (LIKE {target} INCLUDING DEFAULTS) ON COMMIT DROP"
                    ).format(
                        temp=sql.Identifier(temp_table),
                        target=sql.Identifier(table.name),
                    )
                )

                last_columns = None

                # Process file in chunks
                for chunk in read_chunks(path, self.config):
                    valid, invalid = validate_chunk(chunk, input_model)
                    dlq.add(invalid)

                    df = map_rows(valid, field_mapping, computed)
                    if not df.empty:
                        last_columns = list(df.columns)
                        copy_dataframe(cur, df, temp_table)

                # Merge staged data into target
                if last_columns:
                    merge_tables(
                        cur,
                        temp_table,
                        table.name,
                        last_columns,
                        conflict_keys,
                    )

        dlq.flush()
```

---

# 🔹 Usage Example – automatic mapping + overrides + computed fields

```python
from ingestion_framework import Ingestor, IngestionConfig
from datetime import datetime

config = IngestionConfig(chunk_size=5000, separator="\t")

ingestor = Ingestor(conn_params, config)

explicit_mapping = {
    "geonameid": "id",  # Pydantic field → SQLAlchemy field
    "lat": "latitude",
}

computed_fields = {
    "slug": lambda r: r.name.lower().replace(" ", "-"),
    "created_at": lambda _: datetime.utcnow(),
}

ingestor.run(
    file_path="geonames.txt",
    input_model=GeoNameIn,
    db_model=GeoName,
    explicit_mapping=explicit_mapping,
    conflict_keys=["id"],
    computed=computed_fields,
    dlq_path="dead_letters.jsonl",
)
```

✅ Benefits:

1. Automatically maps fields with the same name.
2. Allows explicit overrides when names differ.
3. Adds computed fields for derived or default values.
4. Ignores extra SQLAlchemy columns that are not in Pydantic (as long as they have defaults or nullable).
5. Works in chunked streaming mode with COPY.
6. Supports dead-letter queue for invalid rows.

---

If you want, I can now **rewrite the full framework package structure** using this new mapping logic, so it’s **fully plug-and-play for any table and file**, including optional CLI support.

Do you want me to do that next?

Perfect! Let’s turn your framework into a **fully plug-and-play ingestion package** with the new **automatic + explicit mapping** feature included. I’ll outline the **package structure**, and I’ll include the **actual code for every file**.

This will be ready to install and use in other projects.

---

# 📦 Package: `ingestion_framework`

```
ingestion_framework/
│
├── core/
│   ├── __init__.py
│   ├── ingestor.py          # orchestrator
│   ├── chunking.py          # read file in chunks
│   ├── validation.py        # pydantic validation
│   ├── mapping.py           # automatic + explicit mapping, computed fields
│   ├── copy.py              # COPY to Postgres
│   └── merge.py             # merge staged table into target table
│
├── config/
│   ├── __init__.py
│   └── settings.py          # ingestion configuration
│
├── utils/
│   ├── __init__.py
│   └── dlq.py               # dead-letter queue handling
│
├── interfaces/
│   ├── __init__.py
│   └── schemas.py           # optional base pydantic model
│
├── __init__.py
└── pyproject.toml
```

---

# 🔹 `core/ingestor.py`

```python
from pathlib import Path
from typing import Type

from sqlalchemy.orm import DeclarativeMeta
from pydantic import BaseModel

from ingestion_framework.core.chunking import read_chunks
from ingestion_framework.core.validation import validate_chunk
from ingestion_framework.core.mapping import map_rows, build_mapping
from ingestion_framework.core.copy import copy_dataframe
from ingestion_framework.core.merge import merge_tables
from ingestion_framework.utils.dlq import DeadLetterQueue


class Ingestor:
    def __init__(self, conn_params: dict, config):
        self.conn_params = conn_params
        self.config = config

    def run(
        self,
        file_path: str,
        input_model: Type[BaseModel],
        db_model: Type[DeclarativeMeta],
        explicit_mapping: dict[str, str] | None = None,
        conflict_keys: list[str] | None = None,
        computed: dict | None = None,
        dlq_path: str | None = None,
    ):
        import psycopg2
        from psycopg2 import sql

        path = Path(file_path)
        table = db_model.__table__
        temp_table = f"tmp_{table.name}_stage"

        # Build combined field mapping (auto + explicit)
        field_mapping = build_mapping(input_model, db_model, explicit_mapping)

        # Determine conflict keys if not provided
        if conflict_keys is None:
            conflict_keys = [c.name for c in table.primary_key.columns]

        dlq = DeadLetterQueue(dlq_path)

        with psycopg2.connect(**self.conn_params) as conn:
            with conn.cursor() as cur:
                # Create temp staging table once
                cur.execute(
                    sql.SQL(
                        "CREATE TEMP TABLE {temp} (LIKE {target} INCLUDING DEFAULTS) ON COMMIT DROP"
                    ).format(
                        temp=sql.Identifier(temp_table),
                        target=sql.Identifier(table.name),
                    )
                )

                last_columns = None

                # Process file in chunks
                for chunk in read_chunks(path, self.config):
                    valid, invalid = validate_chunk(chunk, input_model)
                    dlq.add(invalid)

                    df = map_rows(valid, field_mapping, computed)
                    if not df.empty:
                        last_columns = list(df.columns)
                        copy_dataframe(cur, df, temp_table)

                # Merge staged data into target table
                if last_columns:
                    merge_tables(
                        cur,
                        temp_table,
                        table.name,
                        last_columns,
                        conflict_keys,
                    )

        dlq.flush()
```

---

# 🔹 `core/chunking.py`

```python
import pandas as pd
from pathlib import Path


def read_chunks(path: Path, config):
    return pd.read_csv(
        path,
        sep=config.separator,
        chunksize=config.chunk_size,
        header=0 if config.has_header else None,
        dtype=str,
        encoding=config.encoding,
        keep_default_na=False,
    )
```

---

# 🔹 `core/validation.py`

```python
from typing import Type
from pydantic import BaseModel, ValidationError


def validate_chunk(df, model: Type[BaseModel]):
    valid = []
    invalid = []

    for record in df.to_dict(orient="records"):
        try:
            obj = model(**record)
            valid.append(obj)
        except ValidationError as e:
            invalid.append(
                {
                    "raw": record,
                    "error": str(e),
                }
            )

    return valid, invalid
```

---

# 🔹 `core/mapping.py`

```python
from typing import Callable
import pandas as pd


def build_mapping(
    pydantic_model: type,
    sqlalchemy_model,
    explicit_mapping: dict[str, str] | None = None,
) -> dict[str, str]:
    explicit_mapping = explicit_mapping or {}

    sa_columns = {c.name for c in sqlalchemy_model.__table__.columns}
    pyd_fields = set(pydantic_model.model_fields.keys())

    auto_mapped = {
        f: f for f in pyd_fields if f in sa_columns and f not in explicit_mapping
    }

    return {**auto_mapped, **explicit_mapping}


def map_pydantic_to_sqlalchemy(
    row,
    field_mapping: dict[str, str],
    computed_fields: dict[str, Callable] | None = None,
):
    data = row.model_dump()
    mapped_row = {field_mapping[src]: data[src] for src in field_mapping if src in data}

    if computed_fields:
        for field, func in computed_fields.items():
            mapped_row[field] = func(row)

    return mapped_row


def map_rows(
    rows,
    field_mapping: dict[str, str],
    computed_fields: dict[str, Callable] | None = None,
) -> pd.DataFrame:
    mapped = [
        map_pydantic_to_sqlalchemy(r, field_mapping, computed_fields) for r in rows
    ]
    return pd.DataFrame(mapped)
```

---

# 🔹 `core/copy.py`

```python
import io
from psycopg2 import sql


def copy_dataframe(cur, df, temp_table: str):
    if df.empty:
        return

    buffer = io.StringIO()
    df.to_csv(buffer, sep="\t", header=False, index=False, na_rep="")
    buffer.seek(0)

    columns = list(df.columns)

    copy_sql = sql.SQL(
        "COPY {table} ({cols}) FROM STDIN WITH (FORMAT csv, DELIMITER E'\\t', NULL '')"
    ).format(
        table=sql.Identifier(temp_table),
        cols=sql.SQL(", ").join(map(sql.Identifier, columns)),
    )

    cur.copy_expert(copy_sql, buffer)
```

---

# 🔹 `core/merge.py`

```python
from psycopg2 import sql


def merge_tables(
    cur,
    temp_table: str,
    target_table: str,
    columns: list[str],
    conflict_columns: list[str],
):
    update_columns = [c for c in columns if c not in conflict_columns]

    if update_columns:
        updates = sql.SQL(", ").join(
            sql.SQL("{c}=EXCLUDED.{c}").format(c=sql.Identifier(c))
            for c in update_columns
        )
        action = sql.SQL("DO UPDATE SET {updates}").format(updates=updates)
    else:
        action = sql.SQL("DO NOTHING")

    query = sql.SQL("""
        INSERT INTO {target} ({cols})
        SELECT {cols} FROM {temp}
        ON CONFLICT ({conflict})
        {action}
    """).format(
        target=sql.Identifier(target_table),
        temp=sql.Identifier(temp_table),
        cols=sql.SQL(", ").join(map(sql.Identifier, columns)),
        conflict=sql.SQL(", ").join(map(sql.Identifier, conflict_columns)),
        action=action,
    )

    cur.execute(query)
```

---

# 🔹 `config/settings.py`

```python
class IngestionConfig:
    def __init__(
        self,
        chunk_size: int = 10000,
        separator: str = "\t",
        has_header: bool = True,
        encoding: str = "utf-8",
    ):
        self.chunk_size = chunk_size
        self.separator = separator
        self.has_header = has_header
        self.encoding = encoding
```

---

# 🔹 `utils/dlq.py`

```python
import json


class DeadLetterQueue:
    def __init__(self, path: str | None):
        self.path = path
        self.records = []

    def add(self, rows):
        self.records.extend(rows)

    def flush(self):
        if not self.path or not self.records:
            return

        with open(self.path, "w") as f:
            for r in self.records:
                f.write(json.dumps(r) + "\n")
```

---

# 🔹 `interfaces/schemas.py`

```python
from pydantic import BaseModel


class BaseIngestModel(BaseModel):
    class Config:
        extra = "ignore"
```

---

# 🔹 `__init__.py`

```python
from ingestion_framework.core.ingestor import Ingestor
from ingestion_framework.config.settings import IngestionConfig

__all__ = ["Ingestor", "IngestionConfig"]
```

---

# 🔹 Example Usage

```python
from ingestion_framework import Ingestor, IngestionConfig
from datetime import datetime

config = IngestionConfig(chunk_size=5000, separator="\t")
ingestor = In
```
