"""The folder upload load test's tree (#484, scripts/load_folder_upload.py), without a server:
50,000 files in 5,100 folders, the deepest at level 100, every file distinct."""

from scripts.load_folder_upload import depth, file_bytes, make_tree


def test_the_tree_has_50000_files_5100_folders_and_depth_100() -> None:
    folders, files = make_tree()
    assert (len(folders), len(files), depth(folders)) == (5_100, 50_000, 100)
    assert all(
        folder.parent is not None and folder.parent < index for index, folder in enumerate(folders[1:], 1)
    )
    assert len({file_bytes(seq) for seq in range(len(files))}) == 50_000
    assert len(set(files)) == 50_000  # no two files of one folder share a name
