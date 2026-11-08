# macOS schema contract fixture

`macos-plugin-schemas.json` retains only `type`, `items`, and `default` from:

- LivingMemory 2.9.1 `_conf_schema.json`, commit `1c6a471c9af9ae45a4ccb3a8cec5695c917de50b`, https://github.com/lxfight-s-Astrbot-Plugins/astrbot_plugin_livingmemory (AGPL-3.0).
- proactive_chat v1.2.6 `_conf_schema.json`, commit `635fc18eeb84e3c7b58661425359244552286835`, https://github.com/Pancakes-Labs/astrbot_plugin_proactive_chat (AGPL-3.0).
- This repository's affective_core `_conf_schema.json`, investigation baseline `be39b3a076e3051ed0da8930e6606b082482b914`.

Upstream AGPL terms for the derived fixture are included in `LICENSE-AGPL-3.0.txt`; upstream authorship is preserved by the links above. The fixture contains public default prompts, not private conversations, identities, credentials or runtime data. Production initialization reads the pinned source schema and checks its SHA256; it does not use this test fixture.
