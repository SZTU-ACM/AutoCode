"""
编译缓存测试。

测试 CompileCache 类的核心功能。
"""

import os
import tempfile

from autocode_mcp.utils.cache import CompileCache


def test_cache_key_generation():
    """测试缓存键生成：相同输入应生成相同键。"""
    with tempfile.TemporaryDirectory() as tmpdir:
        source_path = os.path.join(tmpdir, "test.cpp")
        with open(source_path, "w", encoding="utf-8") as f:
            f.write("int main() { return 0; }")

        cache = CompileCache(os.path.join(tmpdir, ".cache"))

        key1 = cache._get_key(source_path, "g++", "c++2c", "-O2")
        key2 = cache._get_key(source_path, "g++", "c++2c", "-O2")

        assert key1 == key2
        assert len(key1) == 16


def test_cache_key_differs_with_different_params():
    """测试不同参数生成不同键。"""
    with tempfile.TemporaryDirectory() as tmpdir:
        source_path = os.path.join(tmpdir, "test.cpp")
        with open(source_path, "w", encoding="utf-8") as f:
            f.write("int main() { return 0; }")

        cache = CompileCache(os.path.join(tmpdir, ".cache"))

        key1 = cache._get_key(source_path, "g++", "c++2c", "-O2")
        key2 = cache._get_key(source_path, "clang++", "c++2c", "-O2")
        key3 = cache._get_key(source_path, "g++", "c++17", "-O2")
        key4 = cache._get_key(source_path, "g++", "c++2c", "-O3")

        assert key1 != key2
        assert key1 != key3
        assert key1 != key4


def test_cache_key_differs_with_different_content():
    """测试不同内容生成不同键。"""
    with tempfile.TemporaryDirectory() as tmpdir:
        source1 = os.path.join(tmpdir, "test1.cpp")
        source2 = os.path.join(tmpdir, "test2.cpp")

        with open(source1, "w", encoding="utf-8") as f:
            f.write("int main() { return 0; }")
        with open(source2, "w", encoding="utf-8") as f:
            f.write("int main() { return 1; }")

        cache = CompileCache(os.path.join(tmpdir, ".cache"))

        key1 = cache._get_key(source1, "g++", "c++2c", "-O2")
        key2 = cache._get_key(source2, "g++", "c++2c", "-O2")

        assert key1 != key2


def test_cache_miss():
    """测试缓存未命中：新文件返回 None。"""
    with tempfile.TemporaryDirectory() as tmpdir:
        source_path = os.path.join(tmpdir, "test.cpp")
        with open(source_path, "w", encoding="utf-8") as f:
            f.write("int main() { return 0; }")

        cache = CompileCache(os.path.join(tmpdir, ".cache"))

        result = cache.get(source_path, "g++", "c++2c", "-O2")

        assert result is None


def test_cache_set_and_get():
    """测试缓存存取：可以存储和检索。"""
    with tempfile.TemporaryDirectory() as tmpdir:
        source_path = os.path.join(tmpdir, "test.cpp")
        binary_path = os.path.join(tmpdir, "test.exe")

        with open(source_path, "w", encoding="utf-8") as f:
            f.write("int main() { return 0; }")

        with open(binary_path, "wb") as f:
            f.write(b"fake binary content")

        cache = CompileCache(os.path.join(tmpdir, ".cache"))

        cached_path = cache.set(source_path, binary_path, "g++", "c++2c", "-O2")

        assert os.path.exists(cached_path)
        assert cached_path.startswith(os.path.join(tmpdir, ".cache"))

        retrieved = cache.get(source_path, "g++", "c++2c", "-O2")
        assert retrieved == cached_path
        assert os.path.exists(retrieved)


def test_cache_invalidation_on_content_change():
    """测试内容变更后缓存失效。"""
    with tempfile.TemporaryDirectory() as tmpdir:
        source_path = os.path.join(tmpdir, "test.cpp")
        binary_path = os.path.join(tmpdir, "test.exe")

        with open(source_path, "w", encoding="utf-8") as f:
            f.write("int main() { return 0; }")

        with open(binary_path, "wb") as f:
            f.write(b"fake binary v1")

        cache = CompileCache(os.path.join(tmpdir, ".cache"))

        cache.set(source_path, binary_path, "g++", "c++2c", "-O2")

        with open(source_path, "w", encoding="utf-8") as f:
            f.write("int main() { return 1; }")

        result = cache.get(source_path, "g++", "c++2c", "-O2")

        assert result is None


def test_cache_directory_creation():
    """测试缓存目录自动创建。"""
    with tempfile.TemporaryDirectory() as tmpdir:
        cache_dir = os.path.join(tmpdir, "nested", "cache", "dir")
        CompileCache(cache_dir)

        assert os.path.exists(cache_dir)


def test_cache_path_bucketing():
    """不同源文件应落入不同分桶目录，避免相互污染。"""
    with tempfile.TemporaryDirectory() as tmpdir:
        source1 = os.path.join(tmpdir, "a.cpp")
        source2 = os.path.join(tmpdir, "b.cpp")
        for path in (source1, source2):
            with open(path, "w", encoding="utf-8") as f:
                f.write("int main() { return 0; }")
        binary = os.path.join(tmpdir, "bin")
        with open(binary, "wb") as f:
            f.write(b"x")

        cache = CompileCache(os.path.join(tmpdir, ".cache"))
        cached1 = cache.set(source1, binary, "g++", "c++2c", "-O2")
        cached2 = cache.set(source2, binary, "g++", "c++2c", "-O2")

        # 内容相同但源路径不同，应位于不同分桶目录。
        assert os.path.dirname(cached1) != os.path.dirname(cached2)
        assert cache.get(source1, "g++", "c++2c", "-O2") == cached1
        assert cache.get(source2, "g++", "c++2c", "-O2") == cached2


def test_cache_mtime_fast_path_builds_index():
    """稳定（非最近修改）源文件应在索引中留下 mtime/size 记录以走快速路径。"""
    import json as _json
    import os as _os
    import time as _time

    with tempfile.TemporaryDirectory() as tmpdir:
        source_path = os.path.join(tmpdir, "stable.cpp")
        with open(source_path, "w", encoding="utf-8") as f:
            f.write("int main() { return 0; }")
        # 将 mtime 回拨到安全窗口之外，触发快速路径的索引写入。
        old = _time.time() - 3600
        _os.utime(source_path, (old, old))

        cache = CompileCache(os.path.join(tmpdir, ".cache"))
        key1 = cache._resolve_key(source_path, "g++", "c++2c", "-O2")
        bucket_dir = cache._bucket_dir(source_path)
        index = _json.loads((bucket_dir / "index.json").read_text(encoding="utf-8"))
        entry = index["g++:c++2c:-O2"]
        assert entry["key"] == key1
        assert entry["size"] == os.path.getsize(source_path)

        # 再次解析应命中快速路径，键保持一致。
        key2 = cache._resolve_key(source_path, "g++", "c++2c", "-O2")
        assert key2 == key1
