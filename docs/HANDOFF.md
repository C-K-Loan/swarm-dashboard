# Release handoff

Status: **prepared locally, nothing published.** None of the commands below has been run. Each needs the owner's explicit go.

## Checklist before publishing

- [x] License: Apache-2.0 for the code (`LICENSE`), CC BY 4.0 for the dataset and both adapters (`license: cc-by-4.0` in the cards).
- [x] Provider terms: owner decided to publish everything, with the terms caveat in the dataset and model cards.
- [x] Off-topic sessions: four restored, four (short shell commands and acknowledgements) kept out.
- [ ] Owner has read the internal design notes under `docs/v02/` and `docs/` (see [index.md](index.md)) and is fine publishing them.
- [ ] Final sanitization scans re-run on the exact tree being published (secrets, IPs, hosts, non-project paths, other-project names incl. OCR of images): all zero.
- [ ] Tests pass on that tree: `python3 -m pytest -q scripts ui/tests` and `cd ui && npm run test:unit`.
- [ ] Hugging Face repo names decided (below: `<hf-user>/cct-swarm-gamejam`, `<hf-user>/cct-v03-lean-qwen3-30b-a3b-lora-r16-v1`, `-v2`), and the cross-links in README and cards filled in.
- [ ] The repository is published as a **fresh single-commit history**, never the development history (the development history contains private files).

## 1. Code: fresh repository

Run from a copy of this tree, never from the development worktree.

```bash
cd swarm-dashboard                        # the sanitized tree
test ! -e .env && test ! -d .git          # no secrets file, no old history
git init -b main
git ls-files --others --exclude-standard -z | xargs -0 git add --      # explicit paths, honours .gitignore
git status --short | head -50             # review before committing
git commit -m "CCT Swarm Dashboard: initial public release"
gh repo create <owner>/swarm-dashboard --private --source . --push     # private first
# after a last look on GitHub:
gh repo edit <owner>/swarm-dashboard --visibility public --accept-visibility-change-consequences
```

## 2. Dataset: Hugging Face

```bash
hf auth login                              # interactive, token with write access
hf repo create <hf-user>/cct-swarm-gamejam --repo-type dataset --private
hf upload <hf-user>/cct-swarm-gamejam hf/datasets/cct-swarm-gamejam . --repo-type dataset
```

366 MB, 125 files. `README.md` at the folder root is the dataset card.

## 3. Adapters: Hugging Face

```bash
for v in v1 v2; do
  r=cct-v03-lean-qwen3-30b-a3b-lora-r16-$v
  hf repo create <hf-user>/$r --repo-type model --private
  hf upload <hf-user>/$r hf/models/$r . --repo-type model
done
```

37 MB each: `adapter_model.safetensors`, `adapter_config.json`, tokenizer files, `chat_template.jinja`, `checkpoint.meta`, `README.md` (the model card).

## 4. Make public

Flip each Hugging Face repo to public in its settings (or `hf repo settings <repo> --no-private`, depending on the CLI version) only after the GitHub repository is public and the links between them resolve.

## 5. Static demo

The static build (`scripts/freeze/build.sh`, base `/projects/swarm-board/`) is a folder of HTML, JS and JSON with no backend. It is hosted separately by the owner; nothing in this repository deploys it.
