import os
import time
import json
from datetime import datetime, timezone
import yaml
import requests
import subprocess
import concurrent.futures
from urllib.parse import quote
from pathlib import Path

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
DOWNLOAD_TEST_URL = "https://speed.cloudflare.com/__down?bytes=524288"
DOWNLOAD_BYTES = 524288
MIN_DOWNLOAD_KIB_PER_SEC = 64
DOWNLOAD_TIMEOUT = 18
DOWNLOAD_WORKERS = 8

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


def test_download(node, index, binary):
    """Start an isolated Mihomo instance and verify real response-body transfer."""
    mixed_port = 20000 + index * 2
    api_port = mixed_port + 1
    config_path = f"/tmp/mihomo-download-{index}.yaml"
    name = node["name"]

    config = {
        "mixed-port": mixed_port,
        "allow-lan": False,
        "mode": "rule",
        "log-level": "error",
        "external-controller": f"127.0.0.1:{api_port}",
        "proxies": [node],
        "proxy-groups": [{
            "name": "DOWNLOAD-TEST",
            "type": "select",
            "proxies": [name],
        }],
        "rules": ["MATCH,DOWNLOAD-TEST"],
    }

    process = None
    try:
        with open(config_path, "w", encoding="utf-8") as file:
            yaml.safe_dump(config, file, allow_unicode=True, sort_keys=False)

        process = subprocess.Popen(
            [binary, "-f", config_path],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

        api = f"http://127.0.0.1:{api_port}"
        ready = False
        for _ in range(12):
            if process.poll() is not None:
                return None
            try:
                response = requests.get(api + "/version", timeout=1)
                if response.ok:
                    ready = True
                    break
            except requests.RequestException:
                time.sleep(0.5)

        if not ready:
            return None

        proxy_url = f"http://127.0.0.1:{mixed_port}"
        started = time.monotonic()
        received = 0

        with requests.get(
            DOWNLOAD_TEST_URL,
            proxies={"http": proxy_url, "https": proxy_url},
            headers={
                "Accept-Encoding": "identity",
                "Cache-Control": "no-cache",
                "User-Agent": "node-dashboard-download-check/1.0",
            },
            stream=True,
            timeout=(8, DOWNLOAD_TIMEOUT),
        ) as response:
            if response.status_code != 200:
                return None
            expected = response.headers.get("Content-Length")
            for chunk in response.iter_content(chunk_size=16384):
                if chunk:
                    received += len(chunk)
                    if received > DOWNLOAD_BYTES:
                        return None

        elapsed = max(time.monotonic() - started, 0.001)
        if received != DOWNLOAD_BYTES:
            return None

        speed_kib = round((received / 1024) / elapsed, 1)
        if speed_kib < MIN_DOWNLOAD_KIB_PER_SEC:
            return None

        return {
            "download_kib_per_sec": speed_kib,
            "download_bytes": received,
            "download_seconds": round(elapsed, 2),
        }

    except Exception as error:
        print("下载验证失败：", name, str(error)[:180])
        return None
    finally:
        if process is not None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=3)
        try:
            Path(config_path).unlink(missing_ok=True)
        except OSError:
            pass


def probe_download_endpoint():
    """Confirm the independent download test endpoint itself is reachable."""
    try:
        received = 0
        with requests.get(
            DOWNLOAD_TEST_URL,
            headers={"Accept-Encoding": "identity", "Cache-Control": "no-cache"},
            stream=True,
            timeout=(8, DOWNLOAD_TIMEOUT),
        ) as response:
            if response.status_code != 200:
                return False
            for chunk in response.iter_content(chunk_size=16384):
                if chunk:
                    received += len(chunk)
                    if received > DOWNLOAD_BYTES:
                        return False
        return received == DOWNLOAD_BYTES
    except requests.RequestException as error:
        print("下载测试端点预检失败：", str(error)[:180])
        return False


def build_clash_config(proxy_nodes):
    names = [node["name"] for node in proxy_nodes]
    return {
        "mixed-port": 7890,
        "allow-lan": False,
        "mode": "rule",
        "log-level": "info",
        "proxies": proxy_nodes,
        "proxy-groups": [
            {
                "name": "自动选择",
                "type": "url-test",
                "proxies": names,
                "url": TEST_URL,
                "interval": 300,
                "tolerance": 50,
            },
            {
                "name": "手动选择",
                "type": "select",
                "proxies": ["自动选择", "DIRECT"] + names,
            },
        ],
        "rules": ["MATCH,手动选择"],
    }


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

        download_endpoint_ok = probe_download_endpoint()
        print("下载测试端点预检：", "通过" if download_endpoint_ok else "失败")
        download_passed = []

        if download_endpoint_ok:
            with concurrent.futures.ThreadPoolExecutor(max_workers=DOWNLOAD_WORKERS) as pool:
                futures = {
                    pool.submit(test_download, item["node"], index, binary): item
                    for index, item in enumerate(fast_candidates)
                }
                for future in concurrent.futures.as_completed(futures):
                    item = futures[future]
                    result = future.result()
                    if result:
                        download_passed.append({
                            **item,
                            **result,
                        })
                        print(
                            "下载验证通过：",
                            item["node"]["name"],
                            result["download_kib_per_sec"],
                            "KiB/s",
                        )
        else:
            print("跳过高速节点下载验证：测试端点预检未通过；高速分类本轮不发布未验证节点。")

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
                "download_kib_per_sec": item["download_kib_per_sec"],
                "download_bytes": item["download_bytes"],
                "download_seconds": item["download_seconds"],
            }

        with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
            futures = [
                pool.submit(confirm_fast_stable, item)
                for item in download_passed
            ]
            for future in concurrent.futures.as_completed(futures):
                result = future.result()
                if result:
                    fast_stable.append(result)

        fast_stable.sort(key=lambda item: item["delay"])
        fast_nodes = [item["node"] for item in fast_stable]
        with open("data/fast-stable-nodes.yaml", "w", encoding="utf-8") as file:
            yaml.safe_dump(
                build_clash_config(fast_nodes),
                file, allow_unicode=True, sort_keys=False
            )

        fast_summary = {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "criteria": {
                "max_delay_ms_per_probe": FAST_MAX_DELAY,
                "successful_probes_required": STABILITY_ROUNDS,
                "download_test_url": DOWNLOAD_TEST_URL,
                "download_bytes_required": DOWNLOAD_BYTES,
                "minimum_download_kib_per_sec": MIN_DOWNLOAD_KIB_PER_SEC,
            },
            "working_nodes": len(fast_stable),
            "nodes": [
                {
                    "name": item["node"]["name"],
                    "type": item["node"].get("type", ""),
                    "delay": item["delay"],
                    "samples": item["samples"],
                    "download_kib_per_sec": item["download_kib_per_sec"],
                    "download_bytes": item["download_bytes"],
                    "download_seconds": item["download_seconds"],
                }
                for item in fast_stable
            ],
        }
        with open("data/fast-stable-nodes.json", "w", encoding="utf-8") as file:
            json.dump(fast_summary, file, ensure_ascii=False, indent=2)

        print("高速稳定候选：", len(fast_candidates))
        print("通过完整下载验证：", len(download_passed))
        print(
            "高速稳定节点（3 次延迟测试 + 完整下载 512 KiB + 下载速度至少 64 KiB/s）：",
            len(fast_stable),
        )

        output = build_clash_config([item["node"] for item in passed])

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
