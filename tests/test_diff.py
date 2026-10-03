from graphreview.common.diff import annotate_patch, parse_patch_lines, split_git_diff
from graphreview.common.models import ChangedFile

PATCH = """@@ -10,4 +10,5 @@ def f():
     a = 1
-    b = 2
+    b = 3
+    c = 4
     return a
@@ -30,2 +31,2 @@
-x
+y
 z"""


def test_added_and_context_lines():
    h = parse_patch_lines(PATCH)
    assert h.added == [11, 12, 31]
    assert h.context == [10, 13, 32]
    assert ChangedFile(path="a.py", patch=PATCH).commentable_lines == {10, 11, 12, 13, 31, 32}


def test_annotate_uses_new_side_numbers():
    out = annotate_patch(PATCH).splitlines()
    assert out[1].startswith("   10")
    assert out[2].strip().startswith("-    b = 2")  # removed lines get no number
    assert out[3].startswith("   11 +")


def test_split_git_diff_statuses():
    diff = (
        "diff --git a/x.py b/x.py\nindex 1..2 100644\n--- a/x.py\n+++ b/x.py\n" + PATCH + "\n"
        "diff --git a/new.py b/new.py\nnew file mode 100644\n--- /dev/null\n+++ b/new.py\n@@ -0,0 +1,1 @@\n+print(1)\n"
        "diff --git a/old.py b/old.py\ndeleted file mode 100644\n--- a/old.py\n+++ /dev/null\n@@ -1 +0,0 @@\n-x\n"
    )
    files = {f.path: f for f in split_git_diff(diff)}
    assert files["x.py"].status == "modified" and files["x.py"].additions == 3
    assert files["new.py"].status == "added"
    assert files["old.py"].status == "removed"
