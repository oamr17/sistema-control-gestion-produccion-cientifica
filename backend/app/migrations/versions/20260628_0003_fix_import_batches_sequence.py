from sqlalchemy import text
from sqlalchemy.engine import Engine


VERSION = "20260628_0003_fix_import_batches_sequence"


def upgrade(engine: Engine) -> None:
    if engine.dialect.name != "postgresql":
        return

    with engine.begin() as connection:
        connection.execute(
            text(
                """
                DO $$
                BEGIN
                    IF EXISTS (
                        SELECT 1
                        FROM information_schema.tables
                        WHERE table_name = 'import_batches'
                    ) THEN
                        IF NOT EXISTS (
                            SELECT 1
                            FROM pg_class
                            WHERE relkind = 'S'
                              AND relname = 'import_batches_id_seq'
                        ) THEN
                            CREATE SEQUENCE import_batches_id_seq;
                        END IF;

                        ALTER SEQUENCE import_batches_id_seq OWNED BY import_batches.id;
                        ALTER TABLE import_batches
                            ALTER COLUMN id SET DEFAULT nextval('import_batches_id_seq');

                        PERFORM setval(
                            'import_batches_id_seq',
                            GREATEST(COALESCE((SELECT MAX(id) FROM import_batches), 0) + 1, 1),
                            false
                        );
                    END IF;
                END $$;
                """
            )
        )
