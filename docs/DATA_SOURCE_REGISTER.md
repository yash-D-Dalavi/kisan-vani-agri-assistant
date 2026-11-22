# Data source register

| Source | Used for | Status in prototype | Licence / terms |
|---|---|---|---|
| Seed documents (`seed_data.py`, 27 docs) | Knowledge base | **Original short summaries written for this project**, attributed to the type of publisher they paraphrase (ICAR institutes, KVK, state agricultural universities, PM-KISAN, PMFBY, Kisan Call Centre, Soil Health Card). Not official text. | Demo, educational. Verify against the official source before real use. |
| `rumor_spray_hi` | Test of unapproved content | Fictional WhatsApp-style forward, kept **unapproved** to prove it is never retrieved | n/a |
| Agmarknet via data.gov.in (resource `9ef84268-d588-465a-a308-a864a43d0070`) | Mandi prices | Live only with `AGRI_LIVE=1` and an API key; otherwise a labelled demo feed with simulated numbers | Open Government Data licence; follow data.gov.in terms |
| Open-Meteo | Weather | Live only with `AGRI_LIVE=1`; otherwise a labelled demo feed | Free for non-commercial use, attribution required |
| Kisan Call Centre 1800-180-1551 | Hand-off | Number quoted in refusal messages; confirm it is current before deployment | Government helpline |
| `data/eval_set.json` | Evaluation | 74 questions written by the project author | Project |
| `data/asr_samples.json`, `data/translation_samples.json` | WER / chrF fixtures | Illustrative fixtures, not a benchmark | Project |

Each document row stores source, publisher, date, licence, version, approval and region so any answer can be traced back.
