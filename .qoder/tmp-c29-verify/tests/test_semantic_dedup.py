"""Phase 4.2 建议语义去重器回归测试。

本地无 numpy，测试用纯 Python 列表模拟向量；
semantic_dedup.py 内部在 numpy 可用时用 numpy，不可用时回退纯 Python。
"""

import sys
import os
import math
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from memory_agent.semantic_dedup import SemanticDeduplicator


def cosine_sim_py(v1, v2):
    """纯 Python 余弦相似度。"""
    dot = sum(a * b for a, b in zip(v1, v2))
    n1 = math.sqrt(sum(a * a for a in v1))
    n2 = math.sqrt(sum(a * a for a in v2))
    if n1 == 0 or n2 == 0:
        return 0.0
    return dot / (n1 * n2)


class FakeEmbeddingFunction:
    """Mock embedding 函数：返回纯 Python 列表向量，可控相似度。"""

    def __init__(self, vectors: dict = None):
        self.vectors = vectors or {}
        self.call_count = 0

    def __call__(self, input):
        self.call_count += 1
        if isinstance(input, str):
            input = [input]
        result = []
        for text in input:
            if text in self.vectors:
                result.append(self.vectors[text])
            else:
                # 未注册的文本返回基于 hash 的伪随机向量（低相似度）
                import hashlib
                h = int(hashlib.md5(text.encode()).hexdigest()[:8], 16)
                vec = []
                for i in range(8):
                    h = (h * 1103515245 + 12345) & 0x7fffffff
                    vec.append((h / 0x7fffffff - 0.5) * 2)
                norm = math.sqrt(sum(v * v for v in vec)) or 1
                vec = [v / norm for v in vec]
                result.append(vec)
        return result


class TestSemanticDeduplicatorExact(unittest.TestCase):
    """精确去重（embedding 不可用时）。"""

    def test_exact_duplicate_removed(self):
        deduper = SemanticDeduplicator(config=None, embedding_fn=None)
        result = deduper.dedupe(["建议A", "建议B", "建议A"], threshold=0.85)
        self.assertEqual(result["method"], "exact")
        self.assertEqual(result["unique"], ["建议A", "建议B"])
        self.assertIn("建议A", result["suppressed"])

    def test_no_duplicate(self):
        deduper = SemanticDeduplicator(config=None, embedding_fn=None)
        result = deduper.dedupe(["建议A", "建议B", "建议C"], threshold=0.85)
        self.assertEqual(result["unique"], ["建议A", "建议B", "建议C"])
        self.assertEqual(len(result["suppressed"]), 0)

    def test_empty_input(self):
        deduper = SemanticDeduplicator(config=None, embedding_fn=None)
        result = deduper.dedupe([], threshold=0.85)
        self.assertEqual(result["unique"], [])
        self.assertEqual(result["method"], "none")

    def test_single_input(self):
        deduper = SemanticDeduplicator(config=None, embedding_fn=None)
        result = deduper.dedupe(["只有一条"], threshold=0.85)
        self.assertEqual(result["unique"], ["只有一条"])
        self.assertEqual(result["method"], "exact")


class TestSemanticDeduplicatorSemantic(unittest.TestCase):
    """语义去重（mock embedding，纯 Python 向量）。"""

    def setUp(self):
        # 构造向量：A 和 A' 高相似（cos≈0.9），B 正交
        vec_a = [1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]
        # cos(theta)=0.9 => sin(theta)=sqrt(1-0.81)=sqrt(0.19)≈0.4359
        vec_a_prime = [0.9, 0.4359, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]
        vec_b = [0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]
        self.vectors = {
            "建议A": vec_a,
            "建议A的变体": vec_a_prime,
            "建议B": vec_b,
        }
        self.embed_fn = FakeEmbeddingFunction(self.vectors)
        self.deduper = SemanticDeduplicator(config=None, embedding_fn=self.embed_fn)

    def test_semantic_duplicate_removed(self):
        result = self.deduper.dedupe(["建议A", "建议A的变体", "建议B"], threshold=0.85)
        self.assertEqual(result["method"], "semantic")
        self.assertEqual(result["unique"], ["建议A", "建议B"])
        self.assertIn("建议A的变体", result["suppressed"])
        self.assertEqual(result["suppressed"]["建议A的变体"], "建议A")

    def test_semantic_pairs_recorded(self):
        result = self.deduper.dedupe(["建议A", "建议A的变体"], threshold=0.85)
        self.assertEqual(len(result["pairs"]), 1)
        self.assertEqual(result["pairs"][0]["text"], "建议A的变体")
        self.assertEqual(result["pairs"][0]["duplicate_of"], "建议A")
        self.assertAlmostEqual(result["pairs"][0]["similarity"], 0.9, places=2)

    def test_threshold_below_not_removed(self):
        result = self.deduper.dedupe(["建议A", "建议A的变体"], threshold=0.95)
        self.assertEqual(len(result["unique"]), 2)
        self.assertEqual(len(result["suppressed"]), 0)

    def test_order_preserved(self):
        result = self.deduper.dedupe(["建议A的变体", "建议A"], threshold=0.85)
        self.assertEqual(result["unique"], ["建议A的变体"])
        self.assertIn("建议A", result["suppressed"])


class TestIsDuplicate(unittest.TestCase):
    """is_duplicate 逐条检查。"""

    def test_exact_match(self):
        deduper = SemanticDeduplicator(config=None, embedding_fn=None)
        result = deduper.is_duplicate("建议A", ["建议A", "建议B"], threshold=0.85)
        self.assertTrue(result["duplicate"])
        self.assertEqual(result["duplicate_of"], "建议A")
        self.assertEqual(result["method"], "exact")

    def test_no_match_exact(self):
        deduper = SemanticDeduplicator(config=None, embedding_fn=None)
        result = deduper.is_duplicate("建议C", ["建议A", "建议B"], threshold=0.85)
        self.assertFalse(result["duplicate"])

    def test_semantic_match(self):
        vec_a = [1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]
        vec_a_prime = [0.9, 0.4359, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]
        embed_fn = FakeEmbeddingFunction({"建议A": vec_a, "建议A的变体": vec_a_prime})
        deduper = SemanticDeduplicator(config=None, embedding_fn=embed_fn)
        result = deduper.is_duplicate("建议A的变体", ["建议A"], threshold=0.85)
        self.assertTrue(result["duplicate"])
        self.assertEqual(result["method"], "semantic")
        self.assertAlmostEqual(result["similarity"], 0.9, places=2)

    def test_empty_existing(self):
        deduper = SemanticDeduplicator(config=None, embedding_fn=None)
        result = deduper.is_duplicate("建议A", [], threshold=0.85)
        self.assertFalse(result["duplicate"])
        self.assertEqual(result["method"], "none")


class TestEmbeddingFailure(unittest.TestCase):
    """embedding 失败回退。"""

    def test_embedding_exception_fallback(self):
        class BadEmbedding:
            def __call__(self, input):
                raise RuntimeError("embedding service down")

        deduper = SemanticDeduplicator(config=None, embedding_fn=BadEmbedding())
        result = deduper.dedupe(["建议A", "建议B"], threshold=0.85)
        self.assertIn("fallback", result["method"])
        self.assertEqual(len(result["unique"]), 2)

    def test_embedding_returns_none_fallback(self):
        class NoneEmbedding:
            def __call__(self, input):
                return None

        deduper = SemanticDeduplicator(config=None, embedding_fn=NoneEmbedding())
        result = deduper.dedupe(["建议A", "建议B"], threshold=0.85)
        self.assertIn("fallback", result["method"])


class TestCosineSimilarity(unittest.TestCase):
    """余弦相似度计算（纯 Python 回退路径）。"""

    def test_identical_vectors(self):
        vec = [1.0, 2.0, 3.0]
        sim = cosine_sim_py(vec, vec)
        self.assertAlmostEqual(sim, 1.0, places=5)

    def test_orthogonal_vectors(self):
        vec1 = [1.0, 0.0]
        vec2 = [0.0, 1.0]
        sim = cosine_sim_py(vec1, vec2)
        self.assertAlmostEqual(sim, 0.0, places=5)

    def test_zero_vector(self):
        vec1 = [0.0, 0.0]
        vec2 = [1.0, 1.0]
        sim = cosine_sim_py(vec1, vec2)
        self.assertEqual(sim, 0.0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
