#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""模板管理"""
import json
import os
import uuid
from datetime import datetime
from typing import Optional, List, Dict, Any
import chromadb


def _clean_metadata(source: Dict[str, Any]) -> Dict[str, Any]:
    """把 get_pattern() 的返回值收敛为 Chroma 可接受的扁平 metadata。

    Chroma 只接受 str/int/float/bool 标量，且 'ok' 是接口包装字段不应落库。
    """
    cleaned: Dict[str, Any] = {}
    for key, value in source.items():
        if key == 'ok':
            continue
        if value is None:
            continue
        if isinstance(value, (str, int, float, bool)):
            cleaned[key] = value
        else:
            cleaned[key] = json.dumps(value, ensure_ascii=False)
    cleaned.setdefault('person', '')
    cleaned.setdefault('category', '')
    return cleaned


class PatternManager:
    """模板管理器"""
    
    def __init__(self, config):
        self.config = config
        self.client = chromadb.HttpClient(
            host=config.chroma_host,
            port=config.chroma_port
        )
        self.collection = self.client.get_or_create_collection(
            name="behavior_patterns",
            metadata={"hnsw:space": "cosine"}
        )
        # 确保目录存在
        os.makedirs(config.templates_dir, exist_ok=True)
        os.makedirs(config.imported_dir, exist_ok=True)
    
    def _generate_id(self) -> str:
        """生成唯一ID"""
        return f"pattern_{uuid.uuid4().hex[:12]}"
    
    def list_patterns(
        self,
        person: Optional[str] = None,
        category: Optional[str] = None,
        status: str = "active"
    ) -> Dict[str, Any]:
        """列出模板"""
        # 构建查询条件
        where = {}
        if person:
            where["person"] = person
        if category:
            where["category"] = category
        if status != "all":
            where["status"] = status
        
        # 查询
        if where:
            results = self.collection.query(
                query_texts=["pattern"],
                n_results=1000,
                where=where
            )
        else:
            results = self.collection.get()
        
        # 格式化结果
        patterns = []
        for i, metadata in enumerate(results.get('metadatas', [[]])[0] if results.get('metadatas') else []):
            pattern = {
                "id": results['ids'][0][i] if results.get('ids') else "",
                "person": metadata.get('person', ''),
                "category": metadata.get('category', ''),
                "description": metadata.get('description', ''),
                "status": metadata.get('status', ''),
                "confidence": float(metadata.get('confidence', 0)),
                "sample_count": int(metadata.get('sample_count', 0)),
                "created_at": metadata.get('created_at', ''),
                "last_verified": metadata.get('last_verified', ''),
            }
            patterns.append(pattern)
        
        return {
            "total": len(patterns),
            "patterns": patterns
        }
    
    def get_pattern(self, pattern_id: str) -> Dict[str, Any]:
        """获取单个模板"""
        try:
            result = self.collection.get(ids=[pattern_id])
            if result and result['metadatas']:
                metadata = result['metadatas'][0]
                return {
                    "ok": True,
                    "id": pattern_id,
                    "person": metadata.get('person', ''),
                    "category": metadata.get('category', ''),
                    "description": metadata.get('description', ''),
                    "condition": json.loads(metadata.get('condition', '{}')),
                    "action": json.loads(metadata.get('action', '{}')),
                    "status": metadata.get('status', ''),
                    "confidence": float(metadata.get('confidence', 0)),
                    "sample_count": int(metadata.get('sample_count', 0)),
                    "source": metadata.get('source', ''),
                    "created_at": metadata.get('created_at', ''),
                    "last_verified": metadata.get('last_verified', ''),
                }
            return {"ok": False, "error": "模板不存在"}
        except Exception as e:
            return {"ok": False, "error": str(e)}
    
    def save_pattern(self, pattern: Dict[str, Any]) -> Dict[str, Any]:
        """保存新模板"""
        # 验证必填字段
        required = ['person', 'category', 'condition', 'action']
        for field in required:
            if field not in pattern:
                return {"ok": False, "error": f"缺少必填字段: {field}"}
        
        # 生成ID
        pattern_id = pattern.get('id', self._generate_id())
        
        # 构建元数据
        now = datetime.now().isoformat()
        metadata = {
            "person": pattern['person'],
            "category": pattern['category'],
            "description": pattern.get('description', ''),
            "condition": json.dumps(pattern['condition'], ensure_ascii=False),
            "action": json.dumps(pattern['action'], ensure_ascii=False),
            "status": pattern.get('status', 'pending'),
            "confidence": pattern.get('confidence', 0.5),
            "sample_count": pattern.get('sample_count', 0),
            "source": pattern.get('source', 'master_analysis'),
            "created_at": pattern.get('created_at', now),
            "last_verified": pattern.get('last_verified', now),
        }
        
        # 构建文档（用于向量检索）
        doc = f"{pattern['person']} {pattern['category']} {pattern.get('description', '')}"
        
        # 保存到Chroma
        self.collection.upsert(
            ids=[pattern_id],
            documents=[doc],
            metadatas=[metadata]
        )
        
        return {"ok": True, "id": pattern_id}
    
    def update_pattern(self, pattern_id: str, pattern: Dict[str, Any]) -> Dict[str, Any]:
        """更新模板"""
        # 检查模板是否存在
        existing = self.get_pattern(pattern_id)
        if not existing.get('ok'):
            return {"ok": False, "error": "模板不存在"}
        
        # 更新字段
        now = datetime.now().isoformat()
        metadata = existing.copy()
        metadata.update({
            "person": pattern.get('person', existing.get('person', '')),
            "category": pattern.get('category', existing.get('category', '')),
            "description": pattern.get('description', existing.get('description', '')),
            "condition": json.dumps(pattern.get('condition', existing.get('condition', {})), ensure_ascii=False),
            "action": json.dumps(pattern.get('action', existing.get('action', {})), ensure_ascii=False),
            "confidence": pattern.get('confidence', existing.get('confidence', 0)),
            "sample_count": pattern.get('sample_count', existing.get('sample_count', 0)),
            "last_verified": now,
        })
        
        # 构建文档
        doc = f"{metadata['person']} {metadata['category']} {metadata.get('description', '')}"
        
        # 更新到Chroma
        self.collection.update(
            ids=[pattern_id],
            documents=[doc],
            metadatas=[metadata]
        )
        
        return {"ok": True, "id": pattern_id}
    
    def delete_pattern(self, pattern_id: str, reason: str = "") -> Dict[str, Any]:
        """删除模板"""
        try:
            self.collection.delete(ids=[pattern_id])
            return {"ok": True, "id": pattern_id, "reason": reason}
        except Exception as e:
            return {"ok": False, "error": str(e)}
    
    def confirm_pattern(self, pattern_id: str) -> Dict[str, Any]:
        """确认模板（使其生效）"""
        existing = self.get_pattern(pattern_id)
        if not existing.get('ok'):
            return {"ok": False, "error": "模板不存在"}
        
        metadata = _clean_metadata(existing)
        metadata['status'] = 'active'
        metadata['last_verified'] = datetime.now().isoformat()
        
        # 构建文档
        doc = f"{metadata['person']} {metadata['category']} {metadata.get('description', '')}"
        
        # Chroma 要求 metadatas 为 list，传 dict 会直接报错
        self.collection.update(
            ids=[pattern_id],
            documents=[doc],
            metadatas=[metadata]
        )
        
        return {"ok": True, "id": pattern_id, "status": "active"}
    
    def reject_pattern(self, pattern_id: str, reason: str = "") -> Dict[str, Any]:
        """拒绝模板"""
        existing = self.get_pattern(pattern_id)
        if not existing.get('ok'):
            return {"ok": False, "error": "模板不存在"}
        
        metadata = _clean_metadata(existing)
        metadata['status'] = 'rejected'
        metadata['reject_reason'] = reason
        
        # 构建文档
        doc = f"{metadata['person']} {metadata['category']} {metadata.get('description', '')}"
        
        self.collection.update(
            ids=[pattern_id],
            documents=[doc],
            metadatas=[metadata]
        )
        
        return {"ok": True, "id": pattern_id, "status": "rejected"}
    
    def import_patterns(self, file_path: str) -> Dict[str, Any]:
        """从文件导入模板"""
        try:
            # 读取文件
            with open(file_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            
            # 验证格式
            if 'patterns' not in data:
                return {"ok": False, "error": "文件格式错误：缺少patterns字段"}
            
            # 导入模板
            success_count = 0
            error_count = 0
            errors = []
            
            for pattern in data['patterns']:
                result = self.save_pattern(pattern)
                if result.get('ok'):
                    success_count += 1
                else:
                    error_count += 1
                    errors.append({"pattern": pattern.get('description', ''), "error": result.get('error', '')})
            
            # 移动文件到已导入目录
            filename = os.path.basename(file_path)
            imported_path = os.path.join(self.config.imported_dir, filename)
            os.rename(file_path, imported_path)
            
            return {
                "ok": True,
                "success_count": success_count,
                "error_count": error_count,
                "errors": errors,
                "imported_to": imported_path
            }
        except Exception as e:
            return {"ok": False, "error": str(e)}
    
    def match_pattern(
        self,
        person: str,
        room: str,
        time: Optional[str] = None,
        season: Optional[str] = None
    ) -> Dict[str, Any]:
        """匹配当前场景的模板"""
        # 查询该人员的所有active模板
        results = self.collection.query(
            query_texts=[f"{person} {room}"],
            n_results=100,
            where={"person": person, "status": "active"}
        )
        
        matched = []
        for i, metadata in enumerate(results.get('metadatas', [[]])[0] if results.get('metadatas') else []):
            # 解析条件
            condition = json.loads(metadata.get('condition', '{}'))
            
            # 检查房间
            if condition.get('room') and condition['room'] != room:
                continue
            
            # 检查时间
            if time and condition.get('time_range'):
                # 简单时间范围检查
                start, end = condition['time_range'].split('-')
                if not (start <= time <= end):
                    continue
            
            # 检查季节
            if season and condition.get('season'):
                if season not in condition['season']:
                    continue
            
            # 匹配成功
            pattern = {
                "id": results['ids'][0][i] if results.get('ids') else "",
                "person": metadata.get('person', ''),
                "category": metadata.get('category', ''),
                "description": metadata.get('description', ''),
                "action": json.loads(metadata.get('action', '{}')),
                "confidence": float(metadata.get('confidence', 0)),
            }
            matched.append(pattern)
        
        # 按置信度排序
        matched.sort(key=lambda x: x['confidence'], reverse=True)
        
        return {
            "person": person,
            "room": room,
            "matched_count": len(matched),
            "matched": matched
        }
    
    def record_feedback(
        self,
        pattern_id: str,
        outcome: str,
        details: Optional[str] = None
    ) -> Dict[str, Any]:
        """记录模板执行反馈"""
        existing = self.get_pattern(pattern_id)
        if not existing.get('ok'):
            return {"ok": False, "error": "模板不存在"}
        
        # 更新置信度
        confidence = existing.get('confidence', 0.5)
        if outcome == 'success':
            confidence = min(1.0, confidence + 0.05)
        elif outcome == 'override':
            confidence = max(0.0, confidence - 0.1)
        elif outcome == 'failed':
            confidence = max(0.0, confidence - 0.05)
        
        # 更新模板
        metadata = _clean_metadata(existing)
        metadata['confidence'] = confidence
        metadata['last_verified'] = datetime.now().isoformat()
        if details:
            metadata['last_feedback'] = details
        
        # 构建文档
        doc = f"{metadata['person']} {metadata['category']} {metadata.get('description', '')}"
        
        self.collection.update(
            ids=[pattern_id],
            documents=[doc],
            metadatas=[metadata]
        )
        
        return {
            "ok": True,
            "id": pattern_id,
            "outcome": outcome,
            "new_confidence": confidence
        }
    
    def get_stats(self) -> Dict[str, Any]:
        """获取统计信息"""
        count = self.collection.count()

        # collection.get() 返回的 ids 是扁平列表，取 [0] 会得到单个 id 字符串，
        # len() 结果是字符串长度而非条数 —— 这里直接对列表取长度
        def _count_by_status(status: str) -> int:
            try:
                result = self.collection.get(where={"status": status})
            except Exception:
                return 0
            return len(result.get('ids') or [])

        return {
            "total_patterns": count,
            "active": _count_by_status("active"),
            "pending": _count_by_status("pending")
        }
