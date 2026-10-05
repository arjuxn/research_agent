# Academic Research Agent — Progress

A local academic research agent that discovers university websites, finds the relevant academic department, discovers faculty/staff pages, extracts contact information, and exports the results to Excel/CSV.

The Progress branch builds on V1 with improved website and department discovery, including better handling of ambiguous and blocked results.

## Architecture

```text
University + Discipline
        ↓
Website Candidate Discovery
        ↓
Department Candidate Discovery
        ↓
Faculty / Staff Discovery
        ↓
Contact Extraction
        ↓
Confidence Scoring
        ↓
Excel / CSV Output
```

## Main Components

- `website.py` — Discovers and scores possible university websites.
- `department.py` — Finds and ranks department candidates.
- `faculty.py` — Finds faculty/staff pages.
- `contacts.py` — Extracts names, emails, phones and roles.
- `confidence.py` — Calculates extraction confidence.
- `pdfs.py` — Extracts information from PDFs.
- `llm.py` — Connects to local Qwen3 through Ollama.
- `pipeline.py` — Runs the complete research pipeline.
- `export.py` — Generates Excel/CSV output.
- `main.py` — Command-line interface.

## What's Improved From V1

The Progress branch adds better handling of discovery failures.

### Website Discovery

Multiple website candidates can be discovered and ranked using information such as:

```text
Rank
Score
URL
Source
Reason
```

### Department Discovery

The system can maintain multiple department candidates and distinguish between:

```text
Successful Department
Ambiguous Department
Department Page Blocked
Department Not Found
```

This gives more useful information than simply returning `Field Not Found`.

## Requirements

Install dependencies:

```bash
pip install -r requirements.txt
```

Install Playwright:

```bash
playwright install chromium
```

Install the local LLM:

```bash
ollama pull qwen3:4b
```

Check Ollama:

```bash
ollama list
```

## Run

Start the application:

```bash
python main.py
```

Choose:

```text
1. Single university + discipline
2. Discipline + GEO discovery
3. Batch CSV
4. LLM health check
```

For a basic test:

```text
University: IIT Delhi
Discipline: Computer Science
```

## Output

Results are saved in the `output/` directory.

Typical output:

```text
output/
└── university_contacts.xlsx
```

The output contains information such as:

- University
- Department
- Contact Name
- Email
- Phone
- Contact Type
- Profile URL
- Extraction Method
- Confidence
- Status

## Local LLM

The project uses:

```text
Ollama → Qwen3 4B
```

The LLM is used as a local fallback for tasks where deterministic extraction is insufficient.

## Statuses

The Progress branch supports:

```text
Success
Website Not Found
Field Not Found
Contact Not Found
Ambiguous Department
Department Page Blocked
Error
```

## Limitations

University websites differ significantly in structure and accessibility. Some may block automated requests, require JavaScript, or have incomplete information.

The system therefore keeps candidate scores, confidence values, and failure statuses instead of assuming every discovery is correct.