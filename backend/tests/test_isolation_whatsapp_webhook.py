"""Meta's webhook stores a message only in the organisation of its number's own connection (#603):
one delivery holding two organisations' messages puts each in its own, in its own person's name,
and each organisation's channel run answers only its own number."""

import pytest
from sqlalchemy import text

from kaagaz.adapters.whatsapp import MetaWhatsApp
from kaagaz.channel.loop import Channel
from kaagaz.db.engine import Database
from tests.test_api_support import Api
from tests.whatsapp_fake import (
    NUMBER_KEY,
    PLACEHOLDER,
    Echo,
    connect,
    delivery,
    message,
    new_number,
    post,
    running_fake,
    whatsapp_settings,
)

pytestmark = pytest.mark.db


def test_a_delivery_is_stored_and_answered_only_in_the_organisation_of_each_numbers_connection(
    db: Database,
) -> None:
    with running_fake() as fake:
        api = Api(db, settings=whatsapp_settings(fake))
        people = []
        for _ in range(2):
            customer, _ = api.customer()
            person, _ = api.add_user(customer, "member")
            number = new_number()
            connect(db, customer, person, number)
            people.append((customer, person, number))
        (a, a_person, a_number), (b, b_person, b_number) = people
        both = delivery(message(a_number, api.clock(), "for a"), message(b_number, api.clock(), "for b"))
        assert post(api.client(), both).status_code == 200
        for customer, person, body in ((a, a_person, "for a"), (b, b_person, "for b")):
            with db.user(customer, person) as conn:
                bodies = conn.execute(text("SELECT body FROM whatsapp_messages")).scalars().all()
            assert bodies == [body]
        channel = Channel(api.app_db, MetaWhatsApp.from_settings(api.settings), NUMBER_KEY, api.clock, Echo())
        while claimed := channel.claim(100):
            for linked in claimed:
                channel.handle(linked)
        ours = {a_number.removeprefix("+"), b_number.removeprefix("+")}
        assert sorted(sent for sent in fake.texts() if sent[0] in ours) == sorted(
            [(a_number.removeprefix("+"), PLACEHOLDER), (b_number.removeprefix("+"), PLACEHOLDER)]
        )
