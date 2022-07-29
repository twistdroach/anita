FROM python:3

WORKDIR /usr/src/anita

COPY . .

RUN pip install --no-cache-dir -r requirements.txt
RUN python setup.py install

RUN apt-get update && \
      apt-get install --assume-yes qemu-system gxemul simh man mkisofs \
      bison && \
      rm -rf /var/lib/apt/lists/*

WORKDIR /usr/src/tme
RUN git clone https://github.com/rmblair/tme.git . && \
      ./autogen.sh && \
      ./configure --disable-warnings && \
      make && \
      make install

CMD [ "/usr/local/bin/anita", "-h"  ]
