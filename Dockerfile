FROM python:3.13-alpine
RUN pip install --no-cache-dir -r requirements.txt
COPY pauser.py /pauser.py
CMD ["python", "/pauser.py"]
