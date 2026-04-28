import requests


def ocr_space_file(
    filename,
    overlay=False,
    api_key='helloworld',
    language='eng',
    is_table=False,
    ocr_engine=2,
    timeout=60,
):
    """ OCR.space API request with local file.
        Python3.5 - not tested on 2.7
    :param filename: Your file path & name.
    :param overlay: Is OCR.space overlay required in your response.
                    Defaults to False.
    :param api_key: OCR.space API key.
                    Defaults to 'helloworld'.
    :param language: Language code to be used in OCR.
                    List of available language codes can be found on https://ocr.space/OCRAPI
                    Defaults to 'en'.
    :return: Result in JSON format.
    """

    payload = {
        'isOverlayRequired': overlay,
        'apikey': api_key,
        'language': language,
    }
    advanced_payload = dict(payload)
    advanced_payload['isTable'] = 'true' if is_table else 'false'
    advanced_payload['OCREngine'] = str(ocr_engine)

    endpoint = 'https://api.ocr.space/parse/image'
    with open(filename, 'rb') as f:
        files = {'file': (filename, f, 'application/octet-stream')}

        # Try advanced mode first (table hints + specific OCR engine).
        r = requests.post(endpoint, files=files, data=advanced_payload, timeout=timeout)
        if r.status_code < 400:
            return r.content.decode()

        # Fallback for keys/plans that reject advanced options.
        f.seek(0)
        r_fallback = requests.post(endpoint, files=files, data=payload, timeout=timeout)
        r_fallback.raise_for_status()
        return r_fallback.content.decode()


def ocr_space_url(url, overlay=False, api_key='helloworld', language='eng'):
    """ OCR.space API request with remote file.
        Python3.5 - not tested on 2.7
    :param url: Image url.
    :param overlay: Is OCR.space overlay required in your response.
                    Defaults to False.
    :param api_key: OCR.space API key.
                    Defaults to 'helloworld'.
    :param language: Language code to be used in OCR.
                    List of available language codes can be found on https://ocr.space/OCRAPI
                    Defaults to 'en'.
    :return: Result in JSON format.
    """

    payload = {'url': url,
               'isOverlayRequired': overlay,
               'apikey': api_key,
               'language': language,
               }
    r = requests.post('https://api.ocr.space/parse/image',
                      data=payload,
                      )
    return r.content.decode()


if __name__ == "__main__":
    # Example usage:
    print(ocr_space_file(filename="example_image.png", language="pol"))
    print(ocr_space_url(url="http://i.imgur.com/3cle1d5L5y.jpg"))
