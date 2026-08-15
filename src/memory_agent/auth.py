#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""用户认证系统"""
import json
import os
import tempfile
from datetime import datetime, timedelta, timezone
from typing import Optional, Dict, Any
import bcrypt
from jose import jwt, JWTError

def _hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')

def _verify_password(password: str, password_hash: str) -> bool:
    return bcrypt.checkpw(password.encode('utf-8'), password_hash.encode('utf-8'))

class AuthManager:
    """用户认证管理器"""
    
    def __init__(self, config):
        self.config = config
        self.users_file = config.users_file
        self._ensure_file()
    
    def _ensure_file(self):
        """确保用户文件存在"""
        os.makedirs(os.path.dirname(self.users_file), exist_ok=True)
        if not os.path.exists(self.users_file):
            self._save_users({})
    
    def _load_users(self) -> Dict[str, Any]:
        """加载用户数据"""
        try:
            with open(self.users_file, 'r', encoding='utf-8') as f:
                return json.load(f)
        except:
            return {}
    
    def _save_users(self, users: Dict[str, Any]):
        """保存用户数据（原子写，避免中断损坏账号文件）"""
        directory = os.path.dirname(self.users_file) or "."
        os.makedirs(directory, exist_ok=True)
        tmp_fd, tmp_path = tempfile.mkstemp(prefix=".users-", suffix=".tmp", dir=directory)
        try:
            with os.fdopen(tmp_fd, 'w', encoding='utf-8') as f:
                json.dump(users, f, ensure_ascii=False, indent=2)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp_path, self.users_file)
        except Exception:
            if os.path.exists(tmp_path):
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass
            raise
    
    def register(self, username: str, password: str) -> Dict[str, Any]:
        """注册用户"""
        users = self._load_users()
        
        if username in users:
            return {"ok": False, "error": "用户名已存在"}
        
        if not username or len(username) < 3:
            return {"ok": False, "error": "用户名至少3个字符"}
        
        if not password or len(password) < 6:
            return {"ok": False, "error": "密码至少6个字符"}
        
        users[username] = {
            "password_hash": _hash_password(password),
            "created_at": datetime.now().isoformat(),
            "is_admin": len(users) == 0
        }
        
        self._save_users(users)
        
        return {
            "ok": True,
            "message": "注册成功",
            "is_admin": users[username]["is_admin"]
        }
    
    def login(self, username: str, password: str) -> Dict[str, Any]:
        """登录"""
        users = self._load_users()
        
        if username not in users:
            return {"ok": False, "error": "用户名或密码错误"}
        
        user = users[username]
        
        if not _verify_password(password, user["password_hash"]):
            return {"ok": False, "error": "用户名或密码错误"}
        
        token = self._create_token(username, user.get("is_admin", False))
        
        return {
            "ok": True,
            "token": token,
            "username": username,
            "is_admin": user.get("is_admin", False)
        }
    
    def verify_token(self, token: str) -> Optional[Dict[str, Any]]:
        """验证JWT Token"""
        try:
            payload = jwt.decode(
                token,
                self.config.jwt_secret,
                algorithms=["HS256"]
            )
            username = payload.get("sub")
            if username is None:
                return None
            
            users = self._load_users()
            if username not in users:
                return None
            
            return {
                "username": username,
                "is_admin": users[username].get("is_admin", False)
            }
        except JWTError:
            return None
    
    def _create_token(self, username: str, is_admin: bool) -> str:
        """创建JWT Token"""
        expire = datetime.now(timezone.utc) + timedelta(days=7)
        payload = {
            "sub": username,
            "is_admin": is_admin,
            "exp": expire
        }
        return jwt.encode(payload, self.config.jwt_secret, algorithm="HS256")
    
    def change_password(self, username: str, old_password: str, new_password: str) -> Dict[str, Any]:
        """修改密码"""
        users = self._load_users()
        
        if username not in users:
            return {"ok": False, "error": "用户不存在"}
        
        user = users[username]
        
        if not _verify_password(old_password, user["password_hash"]):
            return {"ok": False, "error": "原密码错误"}
        
        if len(new_password) < 6:
            return {"ok": False, "error": "新密码至少6个字符"}
        
        users[username]["password_hash"] = _hash_password(new_password)
        self._save_users(users)
        
        return {"ok": True, "message": "密码修改成功"}
    
    def get_user(self, username: str) -> Optional[Dict[str, Any]]:
        """获取单个用户的公开信息（不含密码哈希）"""
        user = self._load_users().get(username)
        if not user:
            return None
        return {
            "username": username,
            "is_admin": user.get("is_admin", False),
            "created_at": user.get("created_at", ""),
        }

    def has_users(self) -> bool:
        """是否已存在账号。用于前端决定展示登录还是首次注册。"""
        return bool(self._load_users())

    def list_users(self) -> list:
        """列出所有用户"""
        users = self._load_users()
        return [
            {
                "username": username,
                "is_admin": user.get("is_admin", False),
                "created_at": user.get("created_at", "")
            }
            for username, user in users.items()
        ]
    
    def delete_user(self, username: str) -> Dict[str, Any]:
        """删除用户"""
        users = self._load_users()
        
        if username not in users:
            return {"ok": False, "error": "用户不存在"}
        
        if users[username].get("is_admin"):
            admin_count = sum(1 for u in users.values() if u.get("is_admin"))
            if admin_count <= 1:
                return {"ok": False, "error": "不能删除最后一个管理员"}
        
        del users[username]
        self._save_users(users)
        
        return {"ok": True, "message": "用户删除成功"}
