# Kaagaz-OCR architecture

Kaagaz-OCR reads English business documents, turns them into typed PostgreSQL tables and searchable text, and manages the files. A separate NL-to-SQL agent, built with Hermes, answers questions from that data. Everything is built on a laptop first and later hosted in a dedicated AWS account in Mumbai.

## Design rules

- **Documents stay in our account.** Every model runs on hardware we control. Temporal Cloud, when hosted, sees only IDs.
- **One database holds everything.** Tables, document text and search vectors all live in PostgreSQL, so one query can combine them.
- **Uploads add rows, never tables.** The schema changes only when we add a document type, through a reviewed migration.
- **Every value is traceable.** Each extracted value points to its page, its position on that page and the model that read it.
- **GPUs run only while there's work.** In AWS, a Spot GPU starts when scanned pages are waiting and stops when the queue is empty.
- **Same code on the laptop and in AWS.** Everything runs in Docker; moving to AWS changes settings, not code.

## One upload, step by step

| Step | What happens | Runs on | Writes |
|---|---|---|---|
| Upload | The browser gets a signed link from the API and sends the file straight to storage. The API then starts one Temporal workflow. | API, S3 | `incoming/{upload_id}/` |
| Register | SHA-256 fingerprint; a known file links to the existing document, a new one is filed. | CPU worker | `documents`, `originals/` |
| Route | Spreadsheets and text-layer PDFs stay on CPU; scans and photos go to the GPU queue. | CPU worker | `pages`, `pages/` |
| Parse | Pages become blocks (headings, paragraphs, table cells, stamps, signatures) with positions. PaddleOCR reads scans; Qwen3.5 re-reads handwriting. | CPU and GPU workers | `blocks`, `parsed/` |
| Extract | Qwen3.5 identifies the document type and fills its schema as valid JSON. | GPU worker | typed tables, `field_sources` |
| Validate | Rule checks; one retry on the cropped region; then a review task, and the workflow waits for a person. | CPU worker | `review_tasks` |
| Index | Chunks along sections and tables, each with a context header, embedded with Qwen3-Embedding-0.6B. | CPU worker | `chunks` |

## Components

| Job | On the laptop | In AWS |
|---|---|---|
| API and basic UI | FastAPI in Docker | Same container on an EC2 app server |
| Workflow engine | Temporal dev server | Temporal Cloud |
| Database | PostgreSQL 17 + pgvector in Docker | RDS PostgreSQL, Aurora later |
| File storage | SeaweedFS (S3 API) | Amazon S3 with lifecycle rules |
| Reading scans | PaddleOCR PP-StructureV3 on CPU | PaddleOCR-VL-1.6 on a Spot L4 GPU |
| Handwriting and fields | Qwen3.5 2B or 4B through Ollama | Qwen3.5-9B through vLLM |
| Search embeddings | Qwen3-Embedding-0.6B on CPU | Same, on the app server |
| Sign-in | Dev login | Amazon Cognito |
| Infrastructure | Docker Compose | Terraform and GitHub Actions |

## Where data lives

- **S3** holds the files: `incoming/`, `originals/`, `pages/` and `parsed/`.
- **PostgreSQL** holds everything else, in one schema created once by migrations:
  - Files and management: `uploads`, `documents`, `folders`, `tags`, `document_versions`, `document_links`, `audit_log`.
  - Document content: `pages`, `blocks`.
  - Extraction and quality: `extraction_runs`, typed tables such as `invoices` and `invoice_lines`, `extracted_fields`, `field_sources`, `review_tasks`.
  - Search: `chunks`, with a pgvector embedding and a full-text index.

## How questions get answered

The NL-to-SQL agent connects with a read-only database user. It writes SQL for numbers and lists, and calls a search tool (a small MCP server that runs next to Hermes) for questions about what documents say. It can combine the two: filter documents with SQL, then search only inside them. A data dictionary in Markdown explains each table, column, unit and join.

## Decisions

The architecture page has the full reasoning, including what we rejected and why. New decisions are added here.

- **Qwen, not Gemma 4.** Gemma 4's strongest document model needs a 48 GB GPU, about 2.3 times the hourly cost of the 24 GB L4. The Gemma size that fits an L4 has no clear edge over Qwen3.5-9B on documents, and one model family keeps licensing and upgrades simple.
- **Temporal** runs each document's steps with retries, and survives crashes and waits of several days for review.
- **No separate vector database or cache yet.** pgvector and PostgreSQL full-text search cover search. Aurora, a read replica and ElastiCache come when load needs them.

## Related pages

Private pages; ask for access if a link doesn't open.

- [Document-to-SQL Pipeline](https://claude.ai/artifact/7ATLDAJRxG3Qaab16NhjN8): the full architecture, with diagrams.
- [Five-Day Laptop Build](https://claude.ai/artifact/4TmQt2EUoM784YqgWeyQUo): the day-by-day checklist.
