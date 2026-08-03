---
name: doc-templates
description: How to use the render_template tool and the bundled starter templates — variable substitution stays explicit, missing values are never guessed, and variable-spotting in an imported document is a suggestion, never automatic.
---

# Document templating

## Using `render_template`

Call the `render_template` tool with:

- `template` — the template text, containing `${path.to.var}` placeholders
  (same `${...}` syntax already used elsewhere in PawFlow for flow
  parameters and secret references — nothing new to learn).
- `variables` — a nested JSON object resolved by dotted path, e.g.
  `{"dossier": {"client": "...", "type_affaire": "..."}, "delai": {"date_butoir": "..."}}`.

The tool never guesses a missing value: any placeholder whose path is not
found in `variables` is left as an explicit `[path manquant]` marker in the
rendered text, and the tool's result also lists every missing path
separately (`missing_variables`). Always surface unresolved placeholders to
the user before presenting the draft as ready — a document with a silent
gap is worse than one that visibly flags it.

The tool only transforms text; it never writes a file itself. Present the
rendered draft to the user, or write it yourself with your own tool grants
if asked to save it — always as a draft, never as a sent/filed document
without explicit human validation.

## Bundled starter templates

Three starter templates ship bundled with this skill, under `templates/`
(sibling files of this `SKILL.md`, mounted alongside it wherever the skill
is mounted):
`mise_en_demeure.md`, `accuse_reception.md`, `convocation_rdv.md`. These are
examples to copy and adapt, not fixed content — a firm using this package
should customize them (letterhead, standard clauses, tone) rather than send
them as-is. Treat the shipped copies as a starting point that becomes the
firm's own editable version after first use, never re-render the signed
package content directly for every use if the firm has since edited its own
copy.

## Importing an existing .docx/.pdf as a starting point

When a user wants to turn an existing document into a template:

- **Content-only import** (a .pdf, or a .docx where only the text matters):
  extract the text, drop it into a new template draft, then the user marks
  variable spots themselves by typing `${...}` placeholders into the
  extracted text.
- **Formatting-preserving import** (a .docx with letterhead/layout worth
  keeping): keep the original .docx as the template file; placeholders are
  inserted directly into its text runs, and rendering substitutes those
  runs in place rather than rebuilding the document from markdown. Never
  target a .pdf for formatting-preserving rendering — a PDF has no reliable
  editable structure for in-place substitution.
- **Suggesting variable spots is assisted, never automatic.** You may
  propose likely variable locations (names, dates, amounts detected in
  imported text) as highlighted suggestions to validate one by one — never
  insert them directly. Guessing that a name is a variable is not different
  from guessing a variable's value: both remain a proposal for the user to
  validate, never an applied fact.
