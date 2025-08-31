Cueso is a backend and frontend that allows for using LLM prompts to control a Roku over the local network. It allows for queries like:

* Search for action movies
* Play Severance Season 1 Episode 3
* Play that movie with John Cusack and Jack Black
* Play that episode of I Think You Should Leave with instagram insults

This will require some tool calling to:
1. determine the specific media that needs played
2. determine the content ID in roku
3. play the specific content

Other requirements:
* We should allow for setting preferences on which channels are preferred (channel priority for search results, default channels for certain content types, user-specific channel preferences)
* We will use the Roku ECP (External Control Protocol) API directly, with Roku IP configurable in the backend config
* We should allow for using any LLM provider including a local LLM, and should use a lightweight router for this (LangChain if still state-of-the-art, otherwise a lighter alternative)
* We should allow for voice or text control (multi-modal speech models preferred, but should be pluggable)
* We should allow for additional plugged in MCPs or tool calling for other search use cases
* We should have a separate backend and frontend
* We should have a CLI frontend as well as a web frontend
* The CLI can be more basic than the web frontend
* The web frontend should allow for setting preferences and for doing text or voice commands
* We will build this as a PWA
* We should expect to deploy the backend and frontend separately via Docker (separate containers in same compose setup)
* Either Next.js with typescript or Python is ok. Either way, we will be strongly typed either way and aim for high test coverage and strict linting
* We will build via github actions
* For now, single-user/single-network use, but build hooks for eventual multi-user support
* Media search will integrate with services like TMDb, OMDB, or similar APIs to get metadata before searching Roku
* The LLM will need to make direct tool calls to the Roku API, potentially requiring multiple tool call turns