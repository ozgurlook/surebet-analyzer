from surebet.notifications.preview import build_samples


def test_preview_covers_every_message_the_user_can_receive() -> None:
    titles = [title for title, _ in build_samples()]
    assert titles == [
        "Yeni fırsat kartı",
        "Eşiğe yakın kart",
        "Bayat veri kartı",
        "Kapanmış kart",
        "Düşüş uyarısı",
        "Yükseliş uyarısı",
        "Yeniden açıldı",
        "Kapandı",
    ]


def test_every_sample_renders_non_empty_turkish_text() -> None:
    for _, text in build_samples():
        assert text.strip()
        assert "None" not in text
