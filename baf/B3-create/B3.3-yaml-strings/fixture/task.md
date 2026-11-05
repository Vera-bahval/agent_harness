# YAML string data

Create countries.yaml as a mapping of keys to string values:

| key | value |
|---|---|
| norway | NO |
| ontario | ON |
| disabled | no |
| enabled | on |

Create postal.yaml as a mapping:

| key | value |
|---|---|
| home | 0123 |
| office | 0010 |

All values must remain strings under PyYAML SafeLoader (YAML 1.1). Preserve exact case and leading zeros.
