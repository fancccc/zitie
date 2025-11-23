# -*- coding: utf-8 -*-
import json
import os

DATAS_CONFIG = "./chinese-poetry/loader/datas.json"


class PlainDataLoader():
    def __init__(self, config_path: str = DATAS_CONFIG) -> None:
        self._path = config_path
        with open(config_path, 'r', encoding='utf-8') as config:
            data = json.load(config)
            # self.top_level_path: str = data["cp_path"]
            self.top_level_path: str = './chinese-poetry/'
            self.datasets: dict = data["datasets"]
            self.id_table = {
                v["id"]: k for (k, v) in self.datasets.items()
            }

    def body_extractor(self, target: str) -> list:
        if target not in self.datasets:
            print(f"{target} is not included in datas.json as a dataset")
            return None
        configs = self.datasets[target]
        tag = configs["tag"]
        body = []  # may get a bit huge...
        full_path = os.path.join(self.top_level_path, configs["path"])
        # print(full_path)
        if os.path.isfile(full_path):  # single file json
            with open(full_path, mode='r', encoding='utf-8') as file:
                data = json.load(file)
                for poem in data:
                    body += poem[tag]
            return body
        # a dir, probably with a skip list
        subpaths = os.listdir(full_path)
        for filename in subpaths:
            if filename in configs["excludes"]:
                continue
            with open(os.path.join(full_path, filename), mode='r', encoding='utf-8') as file:
                data = json.load(file)
                for poem in data:
                    body += poem[tag]
        return body

    def extract_from_multiple(self, targets: list) -> list:
        results = []
        for target in targets:
            results += self.body_extractor(target)
        return results

    def extract_with_ids(self, ids: list) -> list:
        results = []
        for id in ids:
            results += self.body_extractor(
                self.id_table[id]
            )
        return results

    def poems_as_text(self, target: str) -> list[str]:
        """
        返回指定数据集中的“每一首诗词”，每首是一个整的字符串（按行拼接）
        需要 datas.json 里该 dataset 的 tag 指向段落字段（比如 paragraphs / content）
        """
        # print(target)
        if target not in self.datasets:
            print(f"{target} is not included in datas.json as a dataset")
            return []

        configs = self.datasets[target]
        # print(configs)
        tag = configs["tag"]
        texts: list[str] = []

        full_path = os.path.join(self.top_level_path, configs["path"])
        # print(full_path)

        def collect_from_file(filepath: str):
            nonlocal texts, tag
            with open(filepath, mode='r', encoding='utf-8') as f:
                data = json.load(f)
                for poem in data:
                    paras = poem.get(tag, [])
                    if isinstance(paras, str):
                        text = paras.strip()
                    elif isinstance(paras, list):
                        # 把段落按行拼成一首
                        text = "\n".join(p for p in paras if isinstance(p, str) and p.strip())
                    else:
                        text = str(paras)
                    if text:
                        title = (f"{poem.get('title', '')}"
                                 f"{poem.get('rhythmic', '')}"
                                 f"{poem.get('chapter', '')}"
                                 f"{poem.get('section', '')}"
                                 f"·{poem.get('author', '')}")
                        texts.append(title + '\n' + text)

        # 单文件
        if os.path.isfile(full_path):
            collect_from_file(full_path)
            return texts

        # 目录
        subpaths = os.listdir(full_path)
        for filename in subpaths:
            if filename in configs.get("excludes", []):
                continue
            collect_from_file(os.path.join(full_path, filename))

        return texts


if __name__ == "__main__":
    loader = PlainDataLoader()
    print(loader.id_table)
    print(loader.poems_as_text('wudai-huajianji'))
    # print(
    #     loader.body_extractor("wudai-huajianji")[-1]
    # )
    # print(
    #     len(loader.extract_from_multiple(["wudai-huajianji", "wudai-nantang"]))
    # )
    # print(
    #     loader.extract_with_ids([0, 1, 2])
    # )

