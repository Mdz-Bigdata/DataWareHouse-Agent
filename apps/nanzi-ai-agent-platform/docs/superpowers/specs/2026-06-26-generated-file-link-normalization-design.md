# Generated File Link Normalization

## Goal

Generated Word and Excel download links must always use the current site origin.
The document service continues to publish a relative capability URL. A model-generated
absolute host, such as `http://yovole.com/...`, must not make a generated file
unavailable from a differently deployed portal.

## Scope

1. In the chat message renderer, recognize generated-file links by the exact route
   prefix `/api/v1/chat/generated-files/`.
2. Convert an absolute URL matching that route into its path, query string, and hash.
   Do not alter any other absolute link.
3. Add an explicit Word/Excel tool instruction that the returned
   `artifact.download_url` must be copied verbatim, with no protocol or host added.
4. Add focused regression coverage for a wrong-host generated-file link and for a
   normal external URL remaining unchanged.

## Design

`GeneratedFileService` is the canonical producer and already returns a relative
capability URL. The browser must therefore resolve it against the current deployed
origin.

`MessageRenderer` is a compatibility boundary for both new streamed replies and
persisted historical replies. Before its existing generic path rewriting runs, it
will parse only `http` and `https` anchors. If their path is a generated-file route,
it will emit the path plus query/hash, discarding the model-supplied origin. This
repairs historical messages without mutating conversation records, while leaving
external links untouched.

The tool descriptions are an additional prompt-level guard, not the correctness
mechanism. No server-side URL host configuration is introduced.

## Validation

Run the focused frontend test covering URL normalization and the existing Python
generated-file tests. Verify that a message containing
`http://yovole.com/api/v1/chat/generated-files/<id>?token=<token>` renders a link
whose href is `/api/v1/chat/generated-files/<id>?token=<token>`, while an ordinary
`https://example.com/...` link is unchanged.
