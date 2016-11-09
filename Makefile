clean:
	-rm -rf doc/build build dist MANIFEST novajoin.egg-info
	-find . -name '*.py[oc]' -exec rm {} \;
.PHONY: clean

sdist: clean
	python setup.py sdist --formats=gztar
.PHONY: sdist

pep8:
	pep8 novajoin scripts

lint:
	pylint -d c,r,i,W0613 -r n -f colorized \
		--notes= \
		--ignored-classes=cherrypy,API \
		--disable=star-args \
		./novajoin
