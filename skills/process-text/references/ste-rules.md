# STE rules the checker uses

Rule numbers are from ASD-STE100 Issue 9. The checker is a set of patterns, so use judgment. Each line says what to do.

## Sentences and paragraphs

| Rule | Check | Fix |
| --- | --- | --- |
| 5.1 | An instruction is longer than 20 words. | Split it. One instruction in each sentence. |
| 6.3 | A descriptive sentence is longer than 25 words. | Split it. One idea in each sentence. |
| 6.6 | A paragraph has more than 6 sentences. | Split it into shorter paragraphs, or use a list. |
| 8.1 | A semicolon. | Write two sentences. |

Word count: text in parentheses, quoted text, code, a number with its unit (`10 kg`), and a hyphenated word each count as one word.

## Instructions

| Rule | Check | Fix |
| --- | --- | --- |
| 5.2 | Two instructions joined by "and" or "then". | Write two sentences, unless the two actions happen at the same time. |
| 5.3 | "You need to...", "You should...", or a step that starts with "You". | Use the imperative: "Run the test." |
| 5.4 | The condition comes after the command. | Put it first: "If the light is on, stop the pump." |

## Verbs

| Rule | Check | Fix |
| --- | --- | --- |
| 3.2 | Perfect tense ("has finished", "have been") or progressive tense ("is running", "are doing"). | Use the simple present or the simple past: "The process finished." "The server runs." |
| 3.5 | An -ing form ("involving", "ensuring"). | Rewrite with a simple verb. Technical nouns are allowed ("the logging system"). |
| 3.6 | Passive voice ("was configured by the administrator"). | Make the agent the subject: "The administrator configured the server." The passive is allowed in descriptions only when the agent is unknown. |
| 1.2 | `should`, `may`, `might`, `would`, `could`, `shall`. | Use `must` for a requirement, `can` for a possibility, `will` for the future, or `if` for a condition. |

Allowed forms: the infinitive, the imperative, the simple present, the simple past, the simple future with `will`, and a past participle used as an adjective ("the installed package").

## Words

| Rule | Check | Fix |
| --- | --- | --- |
| 4.2 | A contraction ("don't", "it's"). | Write the full words: "do not", "it is". `cannot` is one word. |
| 1.2 | `utilize`, `commence`, `ensure`, `prior to`. | `use`, `start`, `make sure`, `before`. |
| 1.14 | British spelling ("colour", "customise"). | American spelling: "color", "customize". |
| GR-6 | `e.g.`, `i.e.`, `etc.` | `for example`, `that is`, `and so on`. |
| GR-7 | `he`, `she`, `his`, `her`. | Use a gender-neutral word: "the user", "they", or rewrite. |

Also: do not drop articles or other words to make a sentence shorter. Use the same word for the same thing each time.

## Never change

Code blocks, inline code, URLs, link targets, file paths, command-line flags, constants (`MAX_SIZE`), version numbers, quoted text, headings, tables, numbers, and every negation (not, never, no).
