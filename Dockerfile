FROM python:3

WORKDIR /usr/src/anita

COPY . .

RUN pip install --no-cache-dir -r requirements.txt
RUN python setup.py install

RUN apt-get update && \
      apt-get install --assume-yes qemu-system gxemul simh man mkisofs && \
      rm -rf /var/lib/apt/lists/*

CMD [ "/usr/local/bin/anita", "-h"  ]
