# Security model

## Authentication

Local passwords use PBKDF2. Tokens are HS256 JWTs. Production refuses a `SECRET_KEY` that starts with `local-dev`. OIDC is a redirect plus callback that matches an existing user by email. There is no automatic account creation.

## Authorization

Permissions are role sets in `app/constants.py`. Admin holds `*`. Routes call `require(permission)`. Account id from the token scopes every query. A caller cannot read another account by passing a different `account_id` filter; analytics returns an empty set.

The periodic review can be completed only when `user.participant_id` equals the task assignee. An administrator who is not that participant is rejected.

## Action governance

Allowlisted actions are the only ones the executor will run. Approval is checked again immediately before each step. Prompt text, uploaded documents, and log lines are untrusted and cannot authorize a tool. Segregation of duties is off unless `SEGREGATION_OF_DUTIES=true`.

## Secrets

ServiceNow passwords are Fernet-encrypted with a key derived from `SECRET_KEY`. API responses never include the password. The frontend stores only the access token in `sessionStorage`. Credentials are not written into the repository.

## Other controls

- Login rate limit per client address
- Idempotency keys and optimistic versions
- Security headers and request ids
- Retention deletes aged application logs, not incidents or audit rows
- Write mode defaults to `read_only`. ServiceNow updates require `approved_write` and a verified response before `SERVICENOW_UPDATE_CONFIRMED`
