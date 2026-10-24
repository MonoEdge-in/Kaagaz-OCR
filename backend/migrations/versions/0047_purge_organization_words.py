"""Deleting an organisation finishes again when it has organisation word suggestions.

``organization_word_suggestions`` (0043) points at ``users`` with NO ACTION. A suggestion
with no document (organisation-wide) is still there when 0018's
``kaagaz_finish_customer_delete`` deletes the users, so the purge failed on the foreign key
and the organisation was never deleted. The finish now deletes them first, as it does
conversations and folders. The body is 0018's with that one line added.

Revision ID: 0047
Revises: 0046
"""

from alembic import op

revision = "0047"
down_revision = "0046"
branch_labels = None
depends_on = None

_FINISH = """
CREATE OR REPLACE FUNCTION public.kaagaz_finish_customer_delete(p_customer uuid) RETURNS boolean
LANGUAGE plpgsql SECURITY DEFINER SET search_path = '' AS $$
BEGIN
    PERFORM 1 FROM public.customers c
    WHERE c.id = p_customer AND c.status = 'deleting' AND c.delete_after <= now() FOR UPDATE;
    IF NOT FOUND OR EXISTS (SELECT 1 FROM public.documents d WHERE d.customer_id = p_customer) THEN
        RETURN false;
    END IF;
    -- Rows pointing at users with NO ACTION go first (documents, and with them
    -- corrections, are gone already); conversations take messages and feedback.
    DELETE FROM public.conversations x WHERE x.customer_id = p_customer;
    DELETE FROM public.learned_corrections x WHERE x.customer_id = p_customer;
    DELETE FROM public.folders x WHERE x.customer_id = p_customer;
    DELETE FROM public.tags x WHERE x.customer_id = p_customer;{words}
    -- users take their words, word spots and stories; the customer takes the rest.
    DELETE FROM public.users x WHERE x.customer_id = p_customer;
    DELETE FROM public.customers c WHERE c.id = p_customer;
    INSERT INTO public.platform_audit (operator_id, action, subject_customer_id, target_type, target_id)
    VALUES (NULL, 'org.purged', p_customer, 'customer', p_customer::text);
    RETURN true;
END
$$
"""
_WORDS = "\n    DELETE FROM public.organization_word_suggestions x WHERE x.customer_id = p_customer;"


def upgrade() -> None:
    op.execute(_FINISH.format(words=_WORDS))


def downgrade() -> None:
    op.execute(_FINISH.format(words=""))  # 0018's body
