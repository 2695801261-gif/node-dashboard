import os
import time
import json
from datetime import datetime, timezone
import yaml
import requests
import subprocess
import concurrent.futures
from urllib.parse import quote

SOURCES = [
    "https://github.com/learnLi/free-nodes/releases/latest/download/list.meta.us.yml",
    "https://radarhub.top/clash.yaml",
    "https://thordata.github.io/awesome-free-proxy-list/data/clash/all.yaml",
]

API = "http://127.0.0.1:9090"
TEST_URL = "https://www.gstatic.com/generate_204"
TIMEOUT = 8000
MAX_WORKERS = 8
FAST_MAX_DELAY = 500
STABILITY_ROUNDS = 3

def fetch_nodes():
    nodes = []
    seen = set()

    for url in SOURCES:
        try:
            print("获取订阅：", url)
            response = requests.get(url, timeout=30)
            response.raise_for_status()
            data = yaml.safe_load(response.text)

            if not isinstance(data, dict):
                continue

            for node in data.get("proxies", []):
                if not isinstance(node, dict):
                    continue

                name = str(node.get("name", "")).strip()
                if not name or not node.get("type"):
                    continue

                if name in seen:
                    continue

                seen.add(name)
                nodes.append(node)

        except Exception as error:
            print("订阅获取失败：", str(error))

    print("获取节点总数：", len(nodes))
    return nodes


def test_node(node):
    name = node["name"]

    try:
        url = (
            API + "/proxies/" + quote(name, safe="")
            + "/delay?url=" + quote(TEST_URL, safe="")
            + "&timeout=" + str(TIMEOUT)
        )

        response = requests.get(url, timeout=12)
        response.raise_for_status()
        result = response.json()
        delay = int(result.get("delay", 0))

        if delay > 0:
            return node, delay

    except Exception:
        pass

    return None


def main():
    os.makedirs("data", exist_ok=True)

    nodes = fetch_nodes()
    if not nodes:
        raise RuntimeError("没有获取到可检测的节点")

    config = {
        "mixed-port": 7890,
        "allow-lan": False,
        "mode": "rule",
        "log-level": "error",
        "external-controller": "127.0.0.1:9090",
        "proxies": nodes,
        "rules": ["MATCH,DIRECT"],
    }

    with open("/tmp/mihomo.yaml", "w", encoding="utf-8") as file:
        yaml.safe_dump(config, file, allow_unicode=True)

    binary = os.environ.get("MIHOMO_BIN", "mihomo")
    process = subprocess.Popen(
        [binary, "-f", "/tmp/mihomo.yaml"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    try:
        for _ in range(30):
            try:
                requests.get(API + "/version", timeout=2)
                break
            except requests.RequestException:
                time.sleep(1)
        else:
            raise RuntimeError("Mihomo 核心启动失败")

        passed = []

        with concurrent.futures.ThreadPoolExecutor(
            max_workers=MAX_WORKERS
        ) as pool:
            futures = [
                pool.submit(test_node, node)
                for node in nodes
            ]

            for future in concurrent.futures.as_completed(futures):
                result = future.result()
                if result:
                    node, delay = result
                    passed.append({"node": node, "delay": delay})
                    print("连接成功：", node["name"], delay, "ms")

        passed.sort(key=lambda item: item["delay"])

        # 独立筛选高速稳定节点：首次测速及后续复测均须成功且不超过阈值
        fast_candidates = [
            item for item in passed
            if item["delay"] <= FAST_MAX_DELAY
        ]
        fast_stable = []

        def confirm_fast_stable(item):
            samples = [item["delay"]]
            for _ in range(STABILITY_ROUNDS - 1):
                result = test_node(item["node"])
                if not result:
                    return None
                _, delay = result
                if delay > FAST_MAX_DELAY:
                    return None
                samples.append(delay)
            return {
                "node": item["node"],
                "delay": round(sum(samples) / len(samples)),
                "samples": samples,
            }

        with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
            futures = [pool.submit(confirm_fast_stable, item) for item in fast_candidates]
            for future in concurrent.futures.as_completed(futures):
                result = future.result()
                if result:
                    fast_stable.append(result)

        fast_stable.sort(key=lambda item: item["delay"])
        with open("data/fast-stable-nodes.yaml", "w", encoding="utf-8") as file:
            yaml.safe_dump(
                {"proxies": [item["node"] for item in fast_stable]},
                file, allow_unicode=True, sort_keys=False
            )

        fast_summary = {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "criteria": {
                "max_delay_ms_per_probe": FAST_MAX_DELAY,
                "successful_probes_required": STABILITY_ROUNDS,
            },
            "working_nodes": len(fast_stable),
            "nodes": [
                {
                    "name": item["node"]["name"],
                    "type": item["node"].get("type", ""),
                    "delay": item["delay"],
                    "samples": item["samples"],
                }
                for item in fast_stable
            ],
        }
        with open("data/fast-stable-nodes.json", "w", encoding="utf-8") as file:
            json.dump(fast_summary, file, ensure_ascii=False, indent=2)

        print("高速稳定候选：", len(fast_candidates))
        print("高速稳定节点（连续三次成功且每次不超过 500 ms）：", len(fast_stable))

        output = {
            "proxies": [item["node"] for item in passed],
        }

        with open(
            "data/checked-nodes.yaml",
            "w",
            encoding="utf-8"
        ) as file:
            yaml.safe_dump(
                output,
                file,
                allow_unicode=True,
                sort_keys=False
            )

        summary = {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "total_nodes": len(nodes),
            "working_nodes": len(passed),
            "test_url": TEST_URL,
            "nodes": [
                {
                    "name": item["node"]["name"],
                    "type": item["node"].get("type", ""),
                    "delay": item["delay"],
                }
                for item in passed
            ],
        }

        with open(
            "data/checked-nodes.json",
            "w",
            encoding="utf-8"
        ) as file:
            json.dump(summary, file, ensure_ascii=False, indent=2)

        print("检测结束")
        print("原始节点：", len(nodes))
        print("连接成功：", len(passed))

        if not passed:
            raise RuntimeError("本轮没有检测到可用节点")

    finally:
        process.terminate()
        process.wait(timeout=10)


if __name__ == "__main__":
    main()
