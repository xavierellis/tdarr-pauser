FROM python:3.13-alpine

# 1) Copy in requirements.txt
COPY requirements.txt /requirements.txt

# 2) Install dependencies
RUN pip install --no-cache-dir -r /requirements.txt

# 3) Copy your script
COPY pauser.py /pauser.py

CMD ["python", "/pauser.py"]
