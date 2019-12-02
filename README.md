# Examples:

- [NameServer](#nameserver)
- [DataNode](#datanode)

### NAMESERVER:

### `[POST] http://127.0.0.1:5000/init-fileserver/`

##### Purpose: Initialize the data-node and list the files this node has.

##### Body (`json_data`): 

```json
{
	"server_ip": "192.168.1.103",
	"server_port": 1488,
	"server_files": ["/var/test/hooy.txt", "/var/test/file3.txt"]
}
```

##### Response:
```json
{
  "allocated": true,
  "rewritten": false
}
```

---
### `[POST] http://127.0.0.1:5000/upload-file/`

##### Purpose: Get the server address to start file uploading

##### Body (`form data`): 

```
filepath : /var/test/hooy.txt
```

##### Response:
```json
{
  "exists": true,
  "fileserver": [
    {
      "server_ip": "192.168.1.103",
      "server_port": 1488
    }
  ]
}
```

---
### `[GET] http://127.0.0.1:5000/get-file/`

##### Purpose: Get the server address to start file downloading.

##### Body (`form data`): 

```
filepath : /var/test/hooy.txt
```

##### Response:
```json
{
  "retrieved": {
    "server_ip": "192.168.1.101",
    "server_port": 1488
  }
}
```

---
### `[GET] http://127.0.0.1:5000/list-dir/`

##### Purpose: List all the files and dirs in the path

##### Body (`form data`): 

```
path : /var/
```

##### Response:
```json
{
  "retrieved": [
    {
      "ancestors": [
        "var"
      ],
      "name": "test",
      "type": "dir"
    },
    {
      "ancestors": [
        "var"
      ],
      "name": ".DS_Store",
      "type": "file"
    }
  ]
}
```

---
### `[GET] http://127.0.0.1:5000/open-dir/`

##### Purpose: Checks if such Dir exists in the namenode database

##### Body (`form data`): 

```
path : /var/
```

##### Response:
On success:
```json
{
  "success": true
}
```
On fail:
`Status 404`
```json
{
  "message": false
}
```

---
### `[POST] http://127.0.0.1:5000/make-dir/`

##### Purpose: Creates directory in the namenode DB

##### Body (`form data`): 

```
path : /var/new_dir/
```

##### Response:
On success:
```json
{
  "success": true
}
```
On fail:
`Status 400`
```json
{
  "message": "Such dir already exists"
}
```


### DATANODE:

---
### `[POST] http://127.0.0.1:10001/upload-file/`

##### Purpose: Upload the file to some data server.

##### Body (`form data`): 

```
filepath : /var/neptune.jpg
file     : `BINARY FILE DATA HERE`
```

##### Response:
```json
{
  "success": true
}
```