# Trip and CSV File Formats

## Trip info file

Supported formats: **JSON** or **YAML**. Required fields: `country`, `city`, `days`. Optional: `flight_info`.

### JSON example

```json
{
  "country": "South Korea",
  "city": "Seoul",
  "days": 5,
  "flight_info": {
    "arrival": "2025-03-10T14:00",
    "departure": "2025-03-15T10:00"
  }
}
```

### YAML example

```yaml
country: South Korea
city: Seoul
days: 5
flight_info:
  arrival: "2025-03-10T14:00"
  departure: "2025-03-15T10:00"
```

### Validation

- `country` (string, required)
- `city` (string, required)
- `days` (integer, required, >= 1)
- `flight_info` (object, optional) — any structure; used for context only

---

## Activity CSV

CSV with optional header. Each row is one vague activity; columns can be:

- **Option A:** Single column with comma-separated parts (e.g. `korea, seoul, salt bread` or `salt bread, seoul`). Preference inferred (e.g. all high) or second column 1–5.
- **Option B:** Two columns: `activity` (vague description), `preference` (1–5 or low/medium/high).

Supported format: UTF-8. At least one data row required. Incomplete info (e.g. "salt bread" without city) is allowed; the research agent will use trip context (city/country) to disambiguate.

### Example (two columns)

```csv
activity,preference
night market Seoul,5
salt bread,4
Gyeongbokgung Palace,3
```

### Example (single column)

```csv
vague_activity
korea, seoul, salt bread
night market, seoul
```

The parser normalizes rows into `{ "activity": "description", "preference": number }`.
