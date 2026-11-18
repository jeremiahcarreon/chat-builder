"""Edit sizes: how much staff changed a draft, ignoring the staff note."""

from app import edits, gate

DRAFT = (
    "Yes, our CC1066 fits the 1966 Chevy II with the 283 V8. It has a 2-row core with 1/2 inch tubes "
    "and comes with the mounting brackets.\n\nSTAFF NOTE: find_fitment listed it under Chevy II, not Nova."
)


def test_the_staff_note_is_split_off():
    reply, note = edits.split_note(DRAFT)
    assert reply.endswith("mounting brackets.") and note == "find_fitment listed it under Chevy II, not Nova."
    assert edits.split_note("**STAFF NOTE:** check stock") == ("", "check stock")
    assert edits.split_note("No note here.") == ("No note here.", None)


def test_removing_the_staff_note_is_no_edit_at_all():
    edit = edits.measure(DRAFT, edits.split_note(DRAFT)[0])
    assert edit == {"changed": 0.0, "removed_only": False, "size": "tiny"}


def test_sizes_follow_the_share_of_words_changed():
    reply = edits.split_note(DRAFT)[0]
    assert edits.measure(DRAFT, reply.replace("Yes,", "Yes!"))["size"] == "tiny"
    cut = edits.measure(DRAFT, reply.replace(" with 1/2 inch tubes", ""))
    assert cut["removed_only"] and cut["size"] == "light"
    assert edits.measure(DRAFT, "Sorry, that one is out of stock until December.")["size"] == "rewrite"


def test_minor_edits_count_as_sent_for_the_gate():
    reply = edits.split_note(DRAFT)[0]
    tiny = {"api_outcome": {"outcome": "edited", "final_text": reply.replace("Yes,", "Yes!")}}
    assert gate.outcome_of(tiny, 0.05, DRAFT) == "sent"
    assert gate.outcome_of(tiny, None, DRAFT) == "edited", "without the setting an edit is an edit"
    cut = {"api_outcome": {"outcome": "edited", "final_text": reply.replace(" with 1/2 inch tubes", "")}}
    assert gate.outcome_of(cut, 0.05, DRAFT) == "sent", "a short cut means the rest was right"
    rewrite = {"api_outcome": {"outcome": "edited", "final_text": "Out of stock until December."}}
    assert gate.outcome_of(rewrite, 0.05, DRAFT) == "edited"
    reviewed = {"review": {"status": "edited", "original": DRAFT}}
    assert gate.outcome_of(reviewed, 0.05, reply) == "sent"
