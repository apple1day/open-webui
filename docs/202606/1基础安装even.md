### 2026年6月17日

```shell
# 当前环境
even@JIAYINGHOU-MC2 open-webui % python3 --version
Python 3.14.6
even@JIAYINGHOU-MC2 open-webui % pip3 --version
pip 26.1.2 from /opt/homebrew/lib/python3.14/site-packages/pip (python 3.14)
even@JIAYINGHOU-MC2 open-webui % 


# 新建环境进行操作
rm -rf ./venv   # 移除之前的虚拟环境（如果存在的话）
python3 -m venv --without-pip ./venv    # 创建一个没有预安装pip的虚拟环境
# 创建完虚拟环境后，激活这个新的虚拟环境并手动安装所需的pip：
source ./venv/bin/activate   # 激活新创建的虚拟环境
curl https://bootstrap.pypa.io/get-pip.py -o get-pip.py    # 下载get-pip脚本
python get-pip.py            # 使用下载好的脚本来安装pip


# 最后还是报错，就不能使用脚本写入mysql配置了，新增  .env 追加下面的内容，再次启动就可以了
DATABASE_URL="mysql+aiomysql://root:123456@127.0.0.1:3306/openwebui" \
DATABASE_SYNC_URL="mysql+pymysql://root:123456@127.0.0.1:3306/openwebui" \
open-webui serve --port 9102

