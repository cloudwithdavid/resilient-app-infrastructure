-- Run once in the configured database using a separate administrative account.
-- The runtime account needs only SELECT, INSERT, and DELETE on this table.
CREATE TABLE IF NOT EXISTS connectivity_probe (
    id CHAR(36) CHARACTER SET ascii COLLATE ascii_bin PRIMARY KEY,
    payload VARCHAR(64) NOT NULL
) ENGINE=InnoDB;

-- Run with the same administrative account, separately from application startup.
CREATE TABLE IF NOT EXISTS contact_submissions (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
    name VARCHAR(80) NOT NULL,
    email VARCHAR(254) NOT NULL,
    subject VARCHAR(160) NOT NULL,
    message TEXT NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
