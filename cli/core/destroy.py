import os
import shutil

from cli.core import config, die, env, load_env, terraform


def run(args):
    if not shutil.which("terraform"):
        die("'terraform' is required but not installed.")

    load_env()
    cfg = config()

    if not os.environ.get("DO_API_TOKEN"):
        die("DO_API_TOKEN is not set in .env")

    # Terraform needs all required variables even for destroy.
    # Resolve each var from env with a "placeholder" fallback.
    for tf_name, env_key in cfg.terraform_vars.items():
        os.environ[f"TF_VAR_{tf_name}"] = env(env_key, "placeholder")

    terraform("destroy", "-auto-approve", "-input=false")

    print()
    print("Infrastructure destroyed.")
