"""Start Hermes Butler server"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import uvicorn
from butler.server import app

if __name__ == '__main__':
    uvicorn.run(app, host='0.0.0.0', port=int(sys.argv[1]) if len(sys.argv) > 1 else 8650, log_level='info')
