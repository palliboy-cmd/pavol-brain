#!/usr/bin/env python3
import json,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).parent))
from fts_baseline import DB,ROOT,evaluate,execute,rebuild
def main():
    build=rebuild(DB); runs=execute(DB); report={'route':'fts5_only','build':build,'queries':runs,'evaluation':evaluate(runs)}
    path=ROOT/'sqlite-spike/results/fts-baseline.json'; path.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n'); print(json.dumps(report['evaluation'],ensure_ascii=False,indent=2))
if __name__=='__main__':main()
