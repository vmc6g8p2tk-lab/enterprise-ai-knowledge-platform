# Authentication and knowledge-base RBAC

## Implemented

`0005_auth_rbac.sql` adds `users` (case-insensitive unique email, bcrypt hash) and `knowledge_base_members` (`admin`, `editor`, `viewer`). Public `POST /auth/register` requires a valid email and a 12–72-byte password; `POST /auth/login` returns a 2-hour HS256 JWT. The secret is read from an untracked `.env`, must be at least 32 bytes, and is never logged. JWT verification fixes the algorithm to HS256 and checks the signature, issuer, audience, timestamps and user existence. A second random token protects Go→Python internal endpoints.

Any authenticated user can create a new isolated knowledge base and is made its first `admin` in the same transaction. This is the bootstrap rule for Phase 3; an existing `viewer` can create **their own new** KB but cannot administer another KB. Existing Phase 1/2 knowledge bases without membership rows are invisible until an administrator explicitly assigns members in the database. New data always starts with an admin.

| Operation | Admin | Editor | Viewer |
| --- | --- | --- | --- |
| List/view own KB, documents, chat, Agent | Yes | Yes | Yes |
| Upload documents | Yes | Yes | No |
| Delete KB/document; list/change members | Yes | No | No |
| Create a new independent KB | Yes | Yes | Yes (becomes its admin) |

Every Go knowledge-base endpoint checks membership. Unauthorized KB IDs return 404 to avoid disclosing existence; insufficient role within a known KB returns 403. The Agent tool layer independently checks the authenticated user's membership and the document's KB before querying. RAG SQL filters by `knowledge_base_id` before Top-K; citation assembly uses only returned in-scope rows. Member edits serialize on the KB row and cannot remove its last admin.

## Actually validated

Full Compose requests covered unauthenticated access, viewer upload denial, editor KB deletion denial, outsider KB denial, URL-swapped KB denial for chat and Agent, member-list filtering, normal editor upload and viewer RAG chat with database-checked in-scope citations. JWT unit tests reject algorithm substitution, payload tampering, another secret and oversized tokens. The unauthorized request regression checks that a 401 is returned without disconnecting.

## Not validated / limitations

Open registration has no email confirmation, brute-force rate limit, password reset, refresh token, logout denylist or organization-wide user provisioning. Roles are KB-local; there is no separate platform administrator. JWTs are stored only in React memory and are lost on page refresh. TLS/reverse-proxy hardening and an external identity provider are outside this local Compose phase. Do not present these controls as production-ready identity management.
