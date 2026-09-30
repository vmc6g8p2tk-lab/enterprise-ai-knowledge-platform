CREATE TABLE users (
    id uuid PRIMARY KEY,
    email text NOT NULL CHECK (length(email) BETWEEN 3 AND 254),
    password_hash text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX users_email_ci_idx ON users (lower(email));

CREATE TABLE knowledge_base_members (
    knowledge_base_id uuid NOT NULL REFERENCES knowledge_bases(id) ON DELETE CASCADE,
    user_id uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    role text NOT NULL CHECK (role IN ('admin','editor','viewer')),
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (knowledge_base_id,user_id)
);
CREATE INDEX knowledge_base_members_user_idx ON knowledge_base_members(user_id,knowledge_base_id);
