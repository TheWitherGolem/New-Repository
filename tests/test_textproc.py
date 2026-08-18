from aloud.textproc import clean_text, split_blocks, split_sentences


def test_hard_wraps_are_unwrapped_but_paragraphs_survive():
    raw = "One line\nwrapped here.\n\nA second paragraph."
    assert clean_text(raw) == "One line wrapped here.\n\nA second paragraph."


def test_words_split_across_lines_are_rejoined():
    assert clean_text("a synthe-\nsiser") == "a synthesiser"


def test_invisible_characters_are_stripped():
    raw = "he​llo﻿ there"
    assert clean_text(raw) == "hello there"


def test_dashes_and_curly_quotes_are_normalised():
    assert clean_text("it’s") == "it's"
    assert clean_text("a—b") == "a - b"
    assert clean_text("wait…") == "wait..."


def test_nonbreaking_spaces_collapse():
    assert clean_text("a  b") == "a b"


def test_empty_input():
    assert clean_text("") == ""
    assert clean_text("   \n  ") == ""


def test_overlong_text_is_cut_at_a_sentence_boundary():
    from aloud import textproc

    text = ("Sentence number one is here. " * 2000)
    cleaned = clean_text(text)
    assert len(cleaned) <= textproc.MAX_CHARS
    assert cleaned.endswith(".")


def test_sentences_split_on_terminators_and_paragraphs():
    text = clean_text('He said "stop!" Then left.\n\nNew para?  Yes.')
    assert split_sentences(text) == ['He said "stop!"', "Then left.", "New para?", "Yes."]


def test_blocks_group_sentences_without_exceeding_the_limit():
    text = "One two three. Four five six. Seven eight nine."
    blocks = split_blocks(text, max_chars=30)
    assert blocks == ["One two three. Four five six.", "Seven eight nine."]
    assert all(len(block) <= 30 for block in blocks)


def test_a_single_oversized_sentence_is_not_chopped():
    sentence = "word " * 100
    blocks = split_blocks(sentence.strip() + ".", max_chars=50)
    assert len(blocks) == 1
