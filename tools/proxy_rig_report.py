"""Generate the fixed Proxy delivery from structured artifacts; no DCC needed."""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from cgi_pipeline.capabilities.maya.asset.proxy_rig.report import write_delivery

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('build', 'before', 'after', 'validation', 'review', 'output'):
        parser.add_argument('--' + name, required=True)
    parser.add_argument('--execution')
    args = parser.parse_args()
    print(write_delivery(args.build,args.before,args.after,args.validation,args.output,args.execution,args.review))

if __name__ == '__main__':
    main()
