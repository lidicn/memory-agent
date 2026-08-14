#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""命令行入口"""
import argparse
import uvicorn

def main():
    parser = argparse.ArgumentParser(description="忆家管家 MCP服务")
    parser.add_argument("command", choices=["serve"], help="运行命令")
    parser.add_argument("--host", default="0.0.0.0", help="监听地址")
    parser.add_argument("--port", type=int, default=8000, help="监听端口")
    
    args = parser.parse_args()
    
    if args.command == "serve":
        uvicorn.run(
            "memory_worker.app:combined_app",
            host=args.host,
            port=args.port,
            reload=False
        )

if __name__ == "__main__":
    main()
