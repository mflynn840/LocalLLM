import argparse
import json
from orchestrator import run

parser = argparse.ArgumentParser(description="Run a checkpoint-supervised OpenHands task.")
parser.add_argument("goal", help="Task for the planner and worker")
parser.add_argument("--mode", choices=("docker", "wsl"), default="docker")
parser.add_argument("--max-reviews", type=int, default=3)
args = parser.parse_args()
print(json.dumps(run(args.goal, args.mode, args.max_reviews), indent=2))
