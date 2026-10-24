# llm/persona/: JARVIS's persona

The system prompt, example exchanges and sampling settings for the talking model. They are chosen by the persona search
(`tools/persona_search/`), which scores candidates automatically and leaves the final choice to the owner's blind rating
of the top three. The full search runs after the training ends (`tests/DEFERRED.md`). Every example exchange is an
original line in JARVIS's style; nothing is quoted from the films.
