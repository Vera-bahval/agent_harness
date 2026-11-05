# Service configuration

Create config.json as a JSON object containing exactly these fields:
- name: the string "agent-service".
- port: the integer 8085.
- debug: the boolean false (not a string).
- retries: the integer 3.
- environments: an array of strings ["dev", "test"] in this order.

No additional fields. Use valid JSON, without comments or duplicate keys.
