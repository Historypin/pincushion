from click.testing import CliRunner

import pincushion


def test_user(tmp_path):
    runner = CliRunner()
    result = runner.invoke(
        pincushion.user, ["--user-id", "120327", "--archive-path", tmp_path]
    )
    assert result.exit_code == 0
    assert (tmp_path / "index.html").is_file()
    assert (tmp_path / "data.json").is_file()
    assert (tmp_path / "index.jpg").is_file()
    assert (tmp_path / "collections" / "san-francisco-1906-2" / "index.html").is_file()
    assert (tmp_path / "collections" / "san-francisco-1906-2" / "image.jpg").is_file()
    assert (tmp_path / "pins" / "1190430" / "index.html").is_file()
    assert (tmp_path / "pins" / "1190430" / "image.jpg").is_file()


def test_collection(tmp_path):
    runner = CliRunner()
    result = runner.invoke(
        pincushion.collection,
        ["--slug", "san-francisco-1906-2", "--archive-path", tmp_path],
    )
    assert result.exit_code == 0
    assert (tmp_path / "index.html").is_file()
    assert (tmp_path / "data.json").is_file()
    assert (tmp_path / "index.jpg").is_file()
    # TODO: at the moment only collections that contain sub-collections works
    # assert (tmp_path / "pins" / "1190430" / "index.html").is_file()
    # assert (tmp_path / "pins" / "1190430" / "image.jpg").is_file()
