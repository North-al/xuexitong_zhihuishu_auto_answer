# Security

## Reporting

If you find a vulnerability in this repository (for example accidental secret leakage in docs or samples), open a GitHub issue **without** pasting live API keys, or contact the maintainer privately.

## Secrets

Do **not** commit:

- `runtime/openai.json` and any file containing API keys
- `.env`
- homework page dumps (`_page_text.txt`, `_zhihuishu_page_text.txt`)
- answer dumps under `runtime/`
- debug screenshots

Use `runtime/openai.example.json` and `.env.example` as templates only.

## Behavior

This app talks to the OpenAI-compatible endpoint **you** configure. It does not submit homework on Chaoxing / Zhihuishu.
